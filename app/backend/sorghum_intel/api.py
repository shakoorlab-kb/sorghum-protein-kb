"""HTTP API and the web console (served from the same origin)."""
from __future__ import annotations

import json
import logging
import shutil
import time
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import agent, auth, documents, graphdb, indexer, retrieval, store
from .config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("sorghum_intel")

app = FastAPI(title="Sorghum Protein Intelligence", docs_url="/api/docs", openapi_url="/api/openapi.json")
Admin = Depends(auth.require_admin)


@app.on_event("startup")
def startup() -> None:
    store.fail_stale_jobs()
    for attempt in range(30):
        try:
            graphdb.ensure_schema()
            log.info("neo4j schema ready")
            return
        except Exception as exc:
            log.warning("waiting for neo4j (%s): %s", attempt, exc)
            time.sleep(4)


# ---- auth ---------------------------------------------------------------------------

class Login(BaseModel):
    username: str
    password: str


@app.post("/api/auth/login")
def login(body: Login, request: Request, response: Response):
    ip = request.headers.get("x-real-ip") or (request.client.host if request.client else "?")
    if not auth.check_password(body.username, body.password, ip):
        raise HTTPException(401, "wrong username or password")
    response.set_cookie(auth.COOKIE, auth.issue(), httponly=True, samesite="lax",
                        max_age=settings.session_days * 86400, path="/")
    return {"user": settings.admin_user}


@app.post("/api/auth/logout")
def logout(response: Response):
    response.delete_cookie(auth.COOKIE, path="/")
    return {"ok": True}


@app.get("/api/auth/me")
def me(request: Request):
    user = auth.current_user(request)
    if not user:
        raise HTTPException(401, "login required")
    return {"user": user, "role": "admin"}


@app.get("/api/health")
def health():
    return {"ok": True, "neo4j": graphdb.ping(), "llm": settings.llm_enabled,
            "chat_model": settings.chat_model, "extract_model": settings.extract_model}


# ---- overview ---------------------------------------------------------------------------

@app.get("/api/stats", dependencies=[Admin])
def stats():
    s = graphdb.stats()
    s["top_entities"] = graphdb.top_entities(24)
    s["usage"] = store.usage_summary()
    s["models"] = {"chat": settings.chat_model, "extract": settings.extract_model, "embed": settings.embed_model}
    s["jobs"] = store.list_jobs(8)
    return s


# ---- documents --------------------------------------------------------------------------

@app.get("/api/documents", dependencies=[Admin])
def list_documents(q: str = "", origin: str | None = None, doc_type: str | None = None,
                   limit: int = 60, offset: int = 0):
    return {"items": graphdb.search_documents(q, min(limit, 300), offset, origin or None, doc_type or None)}


def _doc_or_404(doc_id: str) -> dict:
    d = graphdb.get_document(doc_id)
    if not d:
        raise HTTPException(404, "document not found")
    return d


@app.get("/api/documents/{doc_id}", dependencies=[Admin])
def get_document(doc_id: str):
    return _doc_or_404(doc_id)


@app.get("/api/documents/{doc_id}/claims", dependencies=[Admin])
def document_claims(doc_id: str):
    return {"items": graphdb.document_claims(doc_id)}


@app.get("/api/documents/{doc_id}/graph", dependencies=[Admin])
def document_graph(doc_id: str, limit: int = 50):
    return graphdb.document_graph(doc_id, min(limit, 150))


@app.get("/api/documents/{doc_id}/file", dependencies=[Admin])
def document_file(doc_id: str):
    d = _doc_or_404(doc_id)
    path = Path(d["file_path"])
    if not path.is_file():
        raise HTTPException(404, "original file is missing")
    media = "application/pdf" if path.suffix.lower() == ".pdf" else None
    name = f"{doc_id}{path.suffix}" if d.get("origin") == "upload" else path.name
    return FileResponse(path, media_type=media, filename=name, content_disposition_type="inline")


@app.get("/api/documents/{doc_id}/page/{page}.png", dependencies=[Admin])
def document_page_png(doc_id: str, page: int, dpi: int = 110):
    d = _doc_or_404(doc_id)
    if d.get("file_type") != "pdf":
        raise HTTPException(400, "not a PDF")
    try:
        png = documents.render_page_png(d["file_path"], page, dpi)
    except IndexError:
        raise HTTPException(404, "no such page")
    return Response(png, media_type="image/png", headers={"Cache-Control": "private, max-age=86400"})


@app.get("/api/documents/{doc_id}/page/{page}", dependencies=[Admin])
def document_page(doc_id: str, page: int, quote: str = ""):
    """Everything the viewer needs for one page: its text, its size and the highlight boxes."""
    d = _doc_or_404(doc_id)
    pages = indexer.pages_for(d)
    n = len(pages)
    if not 1 <= page <= max(n, 1):
        raise HTTPException(404, "no such page")
    out = {"doc_id": doc_id, "page": page, "n_pages": n, "file_type": d.get("file_type"),
           "text": pages[page - 1] if pages else "", "rects": [], "width": None, "height": None}
    if quote and d.get("file_type") != "pdf":
        located = documents.locate_quote(pages, quote, page)
        if located and located != page:
            out["suggested_page"] = located
    if d.get("file_type") == "pdf":
        try:
            out["width"], out["height"] = documents.page_size(d["file_path"], page)
            if quote:
                out["rects"] = documents.highlight_rects(d["file_path"], page, quote)
                if not out["rects"]:
                    located = documents.locate_quote(pages, quote, page)
                    if located and located != page:
                        out["suggested_page"] = located
        except Exception as exc:
            out["error"] = str(exc)
    return out


@app.post("/api/documents/{doc_id}/reextract", dependencies=[Admin])
def reextract(doc_id: str):
    _doc_or_404(doc_id)
    return {"job_id": indexer.submit_reextract(doc_id)}


@app.delete("/api/documents/{doc_id}", dependencies=[Admin])
def delete_document(doc_id: str):
    d = _doc_or_404(doc_id)
    graphdb.delete_document(doc_id)
    if d.get("origin") == "upload":
        shutil.rmtree(documents.upload_dir(doc_id), ignore_errors=True)
    return {"ok": True}


# ---- ingestion ---------------------------------------------------------------------------

@app.post("/api/ingest/files", dependencies=[Admin])
async def ingest_files(files: list[UploadFile] = File(...), extract: bool = Form(True)):
    jobs = []
    for f in files:
        data = await f.read()
        if not data:
            continue
        try:
            jobs.append({"file": f.filename, "job_id": indexer.submit_upload(f.filename or "upload", data, extract)})
        except ValueError as exc:
            jobs.append({"file": f.filename, "error": str(exc)})
    return {"jobs": jobs}


class UrlIngest(BaseModel):
    source: str = Field(..., description="a DOI or a direct URL to a PDF/web page")
    extract: bool = True


@app.post("/api/ingest/url", dependencies=[Admin])
def ingest_url(body: UrlIngest):
    if not body.source.strip():
        raise HTTPException(400, "empty source")
    return {"job_id": indexer.submit_url(body.source, body.extract)}


class KbSync(BaseModel):
    extract: bool = True
    limit: int | None = None
    force: bool = False


@app.post("/api/ingest/kb-sync", dependencies=[Admin])
def kb_sync(body: KbSync):
    return {"job_id": indexer.submit_kb_sync(body.extract, body.limit, force=body.force)}


@app.get("/api/jobs", dependencies=[Admin])
def jobs(limit: int = 50):
    return {"items": store.list_jobs(min(limit, 200))}


@app.get("/api/jobs/{jid}", dependencies=[Admin])
def job(jid: str):
    j = store.get_job(jid)
    if not j:
        raise HTTPException(404, "no such job")
    return j


@app.post("/api/jobs/{jid}/cancel", dependencies=[Admin])
def cancel(jid: str):
    indexer.cancel_job(jid)
    return {"ok": True}


# ---- graph ------------------------------------------------------------------------------

@app.get("/api/graph/types", dependencies=[Admin])
def graph_types():
    return graphdb.entity_type_counts()


@app.get("/api/graph/overview", dependencies=[Admin])
def graph_overview(limit: int = 70, type: str | None = None):
    return graphdb.overview(min(limit, 200), type or None)


@app.get("/api/graph/search", dependencies=[Admin])
def graph_search(q: str, limit: int = 20, types: str = ""):
    t = [x for x in types.split(",") if x] or None
    return {"items": graphdb.search_entities(q, min(limit, 50), t)}


@app.get("/api/graph/entity/{eid:path}/neighborhood", dependencies=[Admin])
def entity_neighborhood(eid: str, depth: int = 1, limit: int = 40, min_support: int = 1,
                        types: str = "", documents: bool = False):
    t = [x for x in types.split(",") if x] or None
    return graphdb.neighborhood(eid, depth, min(limit, 120), min_support, t, documents)


@app.get("/api/graph/entity/{eid:path}", dependencies=[Admin])
def entity(eid: str):
    e = graphdb.get_entity(eid)
    if not e:
        raise HTTPException(404, "no such entity")
    return e


@app.get("/api/graph/relation", dependencies=[Admin])
def relation(a: str, b: str):
    return {"items": graphdb.claims_between(a, b, 50)}


@app.get("/api/graph/path", dependencies=[Admin])
def path(a: str, b: str, max_hops: int = 4):
    return graphdb.paths(a, b, max_hops, 6)


@app.get("/api/graph/claim/{cid}", dependencies=[Admin])
def claim(cid: str):
    c = graphdb.get_claim(cid)
    if not c:
        raise HTTPException(404, "no such claim")
    return c


@app.post("/api/graph/subgraph", dependencies=[Admin])
def subgraph(body: dict):
    return graphdb.subgraph(body.get("entity_ids") or [], body.get("doc_ids") or [], int(body.get("expand", 2)))


# ---- search + chat ------------------------------------------------------------------------

@app.get("/api/search", dependencies=[Admin])
def search(q: str, k: int = 12, doc_id: str | None = None):
    return {"items": retrieval.search(q, min(k, 40), [doc_id] if doc_id else None)}


class Ask(BaseModel):
    question: str
    conversation_id: str | None = None


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, default=str)}\n\n"


@app.post("/api/chat", dependencies=[Admin])
def chat(body: Ask):
    q = body.question.strip()
    if not q:
        raise HTTPException(400, "empty question")
    history = store.history(body.conversation_id)
    cid = store.ensure_conversation(body.conversation_id, q)
    store.add_message(cid, "user", q)

    def events():
        yield _sse({"type": "conversation", "conversation_id": cid})
        final = None
        try:
            for ev in agent.run_stream(q, history):
                if ev.get("type") == "final":
                    final = ev["result"]
                yield _sse(ev)
        except Exception as exc:
            log.exception("chat failed")
            yield _sse({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
        if final:
            payload = {k: v for k, v in final.items() if k != "answer"}
            store.add_message(cid, "assistant", final["answer"], payload)
        yield _sse({"type": "done"})

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/conversations", dependencies=[Admin])
def conversations():
    return {"items": store.list_conversations()}


@app.get("/api/conversations/{cid}", dependencies=[Admin])
def conversation(cid: str):
    c = store.get_conversation(cid)
    if not c:
        raise HTTPException(404, "no such conversation")
    return c


@app.delete("/api/conversations/{cid}", dependencies=[Admin])
def delete_conversation(cid: str):
    store.delete_conversation(cid)
    return {"ok": True}


class Rename(BaseModel):
    title: str


@app.patch("/api/conversations/{cid}", dependencies=[Admin])
def rename_conversation(cid: str, body: Rename):
    store.rename_conversation(cid, body.title)
    return {"ok": True}


@app.get("/api/usage", dependencies=[Admin])
def usage():
    return store.usage_summary()


@app.exception_handler(Exception)
def unhandled(request: Request, exc: Exception):
    log.exception("unhandled error on %s", request.url.path)
    return JSONResponse({"detail": f"{type(exc).__name__}: {exc}"}, status_code=500)


# ---- console ------------------------------------------------------------------------------

STATIC = Path(__file__).parent / "static"
if STATIC.is_dir():
    app.mount("/", StaticFiles(directory=STATIC, html=True), name="console")
