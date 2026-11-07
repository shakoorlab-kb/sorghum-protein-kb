"""App-owned SQLite: ingestion jobs, conversations and LLM usage.

Documents themselves live in Neo4j; this file holds operational state only.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from typing import Any, Iterator

from .config import settings

_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS job (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    label TEXT NOT NULL,
    status TEXT NOT NULL,            -- queued | running | done | failed
    progress REAL NOT NULL DEFAULT 0,
    message TEXT NOT NULL DEFAULT '',
    detail_json TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS conversation (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS message (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversation(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_message_conv ON message(conversation_id, created_at);
CREATE TABLE IF NOT EXISTS llm_usage (
    day TEXT NOT NULL,
    purpose TEXT NOT NULL,
    model TEXT NOT NULL,
    calls INTEGER NOT NULL DEFAULT 0,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    cost REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (day, purpose, model)
);
"""


def _connect() -> sqlite3.Connection:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(settings.db_path, timeout=30, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    return con


_con: sqlite3.Connection | None = None


@contextmanager
def db() -> Iterator[sqlite3.Connection]:
    global _con
    with _lock:
        if _con is None:
            _con = _connect()
            _con.executescript(SCHEMA)
        try:
            yield _con
            _con.commit()
        except Exception:
            _con.rollback()
            raise


def _row(r: sqlite3.Row | None) -> dict[str, Any] | None:
    if r is None:
        return None
    d = dict(r)
    for key in ("detail_json", "payload_json"):
        if key in d:
            d[key.removesuffix("_json")] = json.loads(d.pop(key) or "{}")
    return d


# ---- jobs -----------------------------------------------------------------

def create_job(kind: str, label: str, detail: dict | None = None) -> str:
    jid = uuid.uuid4().hex[:12]
    now = time.time()
    with db() as c:
        c.execute(
            "INSERT INTO job VALUES (?,?,?,?,?,?,?,?,?)",
            (jid, kind, label, "queued", 0, "", json.dumps(detail or {}), now, now),
        )
    return jid


def update_job(jid: str, *, status: str | None = None, progress: float | None = None,
               message: str | None = None, detail: dict | None = None) -> None:
    sets, args = ["updated_at=?"], [time.time()]
    if status is not None:
        sets.append("status=?"); args.append(status)
    if progress is not None:
        sets.append("progress=?"); args.append(max(0.0, min(1.0, progress)))
    if message is not None:
        sets.append("message=?"); args.append(message[:2000])
    if detail is not None:
        sets.append("detail_json=?"); args.append(json.dumps(detail))
    args.append(jid)
    with db() as c:
        c.execute(f"UPDATE job SET {', '.join(sets)} WHERE id=?", args)


def get_job(jid: str) -> dict | None:
    with db() as c:
        return _row(c.execute("SELECT * FROM job WHERE id=?", (jid,)).fetchone())


def list_jobs(limit: int = 50) -> list[dict]:
    with db() as c:
        rows = c.execute("SELECT * FROM job ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    return [_row(r) for r in rows]


def fail_stale_jobs() -> None:
    """Jobs left 'running' by a restart can never finish; say so."""
    with db() as c:
        c.execute(
            "UPDATE job SET status='failed', message='interrupted by a server restart' "
            "WHERE status IN ('queued','running')"
        )


# ---- conversations ----------------------------------------------------------

def ensure_conversation(cid: str | None, title: str) -> str:
    now = time.time()
    with db() as c:
        if cid and c.execute("SELECT 1 FROM conversation WHERE id=?", (cid,)).fetchone():
            c.execute("UPDATE conversation SET updated_at=? WHERE id=?", (now, cid))
            return cid
        cid = cid or uuid.uuid4().hex
        c.execute("INSERT INTO conversation VALUES (?,?,?,?)", (cid, title[:120], now, now))
    return cid


def add_message(cid: str, role: str, content: str, payload: dict | None = None) -> str:
    mid = uuid.uuid4().hex
    with db() as c:
        c.execute(
            "INSERT INTO message VALUES (?,?,?,?,?,?)",
            (mid, cid, role, content, json.dumps(payload or {}), time.time()),
        )
    return mid


def list_conversations(limit: int = 100) -> list[dict]:
    with db() as c:
        rows = c.execute(
            "SELECT c.*, (SELECT COUNT(*) FROM message m WHERE m.conversation_id=c.id) AS n_messages "
            "FROM conversation c ORDER BY updated_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_conversation(cid: str) -> dict | None:
    with db() as c:
        conv = c.execute("SELECT * FROM conversation WHERE id=?", (cid,)).fetchone()
        if not conv:
            return None
        msgs = c.execute(
            "SELECT * FROM message WHERE conversation_id=? ORDER BY created_at", (cid,)
        ).fetchall()
    return {**dict(conv), "messages": [_row(m) for m in msgs]}


def history(cid: str | None, turns: int = 6) -> list[dict]:
    if not cid:
        return []
    with db() as c:
        rows = c.execute(
            "SELECT role, content FROM message WHERE conversation_id=? ORDER BY created_at DESC LIMIT ?",
            (cid, turns * 2),
        ).fetchall()
    return [dict(r) for r in reversed(rows)]


def delete_conversation(cid: str) -> None:
    with db() as c:
        c.execute("DELETE FROM conversation WHERE id=?", (cid,))


def rename_conversation(cid: str, title: str) -> None:
    with db() as c:
        c.execute("UPDATE conversation SET title=? WHERE id=?", (title[:120], cid))


# ---- LLM usage ----------------------------------------------------------------

def record_usage(purpose: str, model: str, prompt_tokens: int, completion_tokens: int, cost: float) -> None:
    day = time.strftime("%Y-%m-%d")
    with db() as c:
        c.execute(
            "INSERT INTO llm_usage VALUES (?,?,?,1,?,?,?) ON CONFLICT(day,purpose,model) DO UPDATE SET "
            "calls=calls+1, prompt_tokens=prompt_tokens+excluded.prompt_tokens, "
            "completion_tokens=completion_tokens+excluded.completion_tokens, cost=cost+excluded.cost",
            (day, purpose, model, prompt_tokens, completion_tokens, cost),
        )


def usage_summary() -> dict:
    with db() as c:
        total = c.execute(
            "SELECT COALESCE(SUM(calls),0) calls, COALESCE(SUM(cost),0) cost, "
            "COALESCE(SUM(prompt_tokens),0) prompt_tokens, COALESCE(SUM(completion_tokens),0) completion_tokens "
            "FROM llm_usage"
        ).fetchone()
        by = c.execute(
            "SELECT purpose, model, SUM(calls) calls, SUM(cost) cost FROM llm_usage GROUP BY purpose, model"
        ).fetchall()
    return {**dict(total), "by_purpose": [dict(r) for r in by]}
