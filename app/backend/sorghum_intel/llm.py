"""OpenRouter client (OpenAI-compatible chat completions), same key as Contract Intelligence.

Two calls: `complete_json` for structured steps (planning, graph extraction) and
`stream` for the answer text. Usage and cost are recorded per purpose.
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Iterator

import httpx

from . import store
from .config import settings


class LLMError(RuntimeError):
    pass


_client: httpx.Client | None = None


def _http() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(
            base_url=settings.llm_base_url,
            timeout=httpx.Timeout(settings.llm_timeout_s, connect=15),
            headers={
                "Authorization": f"Bearer {settings.llm_api_key}",
                "HTTP-Referer": os.environ.get("SI_PUBLIC_URL", "http://localhost:8000/"),
                "X-Title": "Sorghum Protein Intelligence",
            },
            limits=httpx.Limits(max_connections=64, max_keepalive_connections=32),
        )
    return _client


def _record(purpose: str, model: str, usage: dict | None) -> None:
    if not usage:
        return
    store.record_usage(
        purpose, model,
        int(usage.get("prompt_tokens") or 0),
        int(usage.get("completion_tokens") or 0),
        float(usage.get("cost") or 0.0),
    )


def _post(body: dict, attempts: int = 3) -> dict:
    if not settings.llm_enabled:
        raise LLMError("OPENROUTER_API_KEY is not set")
    last: Exception | None = None
    for i in range(attempts):
        try:
            r = _http().post("/chat/completions", json=body)
            if r.status_code in (429, 500, 502, 503, 504):
                raise LLMError(f"{r.status_code}: {r.text[:300]}")
            if r.status_code >= 400:
                raise LLMError(f"{r.status_code}: {r.text[:500]}")
            data = r.json()
            if "error" in data:
                raise LLMError(str(data["error"])[:500])
            return data
        except (httpx.HTTPError, LLMError, json.JSONDecodeError) as exc:
            last = exc
            time.sleep(1.5 * (i + 1) ** 2)
    raise LLMError(str(last))


def complete(messages: list[dict], *, purpose: str, model: str | None = None,
             max_tokens: int = 2000, temperature: float = 0.0, json_mode: bool = False) -> str:
    model = model or settings.chat_model
    body: dict = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "usage": {"include": True},
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    data = _post(body)
    _record(purpose, model, data.get("usage"))
    try:
        return data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError) as exc:
        raise LLMError(f"unexpected response: {str(data)[:300]}") from exc


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.M)


def parse_json(text: str) -> dict:
    text = _FENCE.sub("", text.strip())
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start:end + 1])
        raise


def complete_json(messages: list[dict], *, purpose: str, model: str | None = None,
                  max_tokens: int = 3000) -> dict:
    text = complete(messages, purpose=purpose, model=model, max_tokens=max_tokens, json_mode=True)
    try:
        return parse_json(text)
    except json.JSONDecodeError:
        # One repair attempt: ask the model to return only the JSON.
        fixed = complete(
            messages + [{"role": "assistant", "content": text},
                        {"role": "user", "content": "That was not valid JSON. Return only the corrected JSON object."}],
            purpose=purpose, model=model, max_tokens=max_tokens, json_mode=True,
        )
        return parse_json(fixed)


def stream(messages: list[dict], *, purpose: str, model: str | None = None,
           max_tokens: int = 2500, temperature: float = 0.1) -> Iterator[str]:
    """Yield answer text as it arrives."""
    if not settings.llm_enabled:
        raise LLMError("OPENROUTER_API_KEY is not set")
    model = model or settings.chat_model
    body = {
        "model": model, "messages": messages, "max_tokens": max_tokens,
        "temperature": temperature, "stream": True, "usage": {"include": True},
    }
    usage = None
    with _http().stream("POST", "/chat/completions", json=body) as r:
        if r.status_code >= 400:
            raise LLMError(f"{r.status_code}: {r.read().decode(errors='replace')[:500]}")
        for line in r.iter_lines():
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                chunk = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if chunk.get("usage"):
                usage = chunk["usage"]
            for choice in chunk.get("choices") or []:
                piece = (choice.get("delta") or {}).get("content")
                if piece:
                    yield piece
    _record(purpose, model, usage)
