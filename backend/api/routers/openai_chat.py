"""
OpenAI-compatible chat completions — the other half of the provider seam.

``openai_compat.py`` already makes this backend a drop-in OpenAI **audio**
provider (``/v1/audio/speech``, ``/v1/audio/transcriptions``). This module adds
the chat half, so a tool that already speaks OpenAI's protocol can point its
"custom provider" base URL at ``http://localhost:3900/v1`` and get text
completions from whatever LLM this install is already configured to use —
Ollama, LM Studio, OpenAI, or any other OpenAI-compatible host behind
``TRANSLATE_BASE_URL``.

    POST /v1/chat/completions   → chat, streaming or one-shot
    GET  /v1/models             → the one model this install actually serves

This is a **relay, not a model**. It owns no inference: every request is
forwarded to ``services.llm_backend.get_active_llm_backend()``, the same
adapter the dubbing translator, the glossary extractor, and the voice agents
use. That is the point — one place configures the LLM, and every consumer
(including external ones) inherits it.

Two deliberate deviations from OpenAI's API, both visible rather than silent:

* **``model`` in the request is advisory.** An install serves exactly one
  configured model; there is nothing to switch between. The requested value is
  ignored and the response's ``model`` field reports what actually ran, so a
  client is never told it got a model it did not get.
* **Unknown request fields are accepted and ignored** (``reasoning_effort``,
  ``enable_thinking``, ``store``, …). Clients probe providers by sending their
  full parameter set; rejecting an unrecognised knob with a 422 would fail the
  connection test on a provider that works fine. What we cannot honour, we do
  not pretend to — but we also do not refuse the request over it.

Auth is the app-wide ``BearerKeyMiddleware``: unset ``OMNIVOICE_API_KEY`` and
loopback clients need no key, set it and every non-loopback caller must present
it. Nothing here adds or bypasses a check.

Reference: https://platform.openai.com/docs/api-reference/chat
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import iterate_in_threadpool, run_in_threadpool

logger = logging.getLogger("omnivoice.openai_chat")

router = APIRouter(prefix="/v1", tags=["OpenAI-Compatible Chat API"])


# ── Schemas ─────────────────────────────────────────────────────────────────


class ChatMessage(BaseModel):
    """One turn. ``content`` is text-only: this relay has no vision path."""

    role: str = Field(..., description="'system', 'user', or 'assistant'.")
    content: str = Field(..., description="The message text.")


class ChatCompletionRequest(BaseModel):
    """POST /v1/chat/completions — mirrors OpenAI's CreateChatCompletionRequest.

    ``extra="allow"`` is load-bearing, not laziness: see the module docstring.
    """

    model_config = ConfigDict(extra="allow")

    model: Optional[str] = Field(
        default=None,
        description=(
            "Advisory only — this install serves one configured model. The "
            "response reports the model that actually ran."
        ),
    )
    messages: list[ChatMessage] = Field(..., min_length=1)
    stream: bool = Field(default=False, description="Server-sent events when true.")
    temperature: Optional[float] = Field(default=None, ge=0.0, le=2.0)
    max_tokens: Optional[int] = Field(default=None, gt=0)
    max_completion_tokens: Optional[int] = Field(
        default=None,
        gt=0,
        description="OpenAI's newer name for max_tokens. Whichever is set is used.",
    )

    def token_limit(self) -> Optional[int]:
        """The effective cap, under either of OpenAI's two spellings."""
        return self.max_completion_tokens or self.max_tokens


# ── Helpers ─────────────────────────────────────────────────────────────────


def _resolve_backend():
    """The active LLM adapter, or a 503 a client can actually act on.

    ``OffBackend`` raises at call time with a message naming the env var to
    set. Surfacing that verbatim beats a generic 'no backend' — the caller is
    usually someone wiring up a provider for the first time.
    """
    from services.llm_backend import get_active_llm_backend

    try:
        return get_active_llm_backend()
    except Exception as exc:  # unknown backend id in prefs/env
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _completion_id() -> str:
    return f"chatcmpl-{uuid.uuid4().hex[:24]}"


def _model_name(backend) -> str:
    """Never let a broken adapter turn into a 500 over a label."""
    try:
        return backend.model_name
    except Exception:
        return "unknown"


def _chunk(cid: str, created: int, model: str, delta: dict[str, Any],
           finish_reason: Optional[str] = None) -> str:
    """One SSE frame in OpenAI's streaming shape."""
    payload = {
        "id": cid,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }
    return f"data: {json.dumps(payload)}\n\n"


# ── POST /v1/chat/completions ───────────────────────────────────────────────


@router.post("/chat/completions")
async def chat_completions(req: ChatCompletionRequest):
    """Chat completion, streamed or one-shot, against the configured LLM."""
    backend = _resolve_backend()
    model = _model_name(backend)
    messages = [m.model_dump() for m in req.messages]
    limit = req.token_limit()

    if req.stream:
        return StreamingResponse(
            _stream(backend, model, messages, req.temperature, limit),
            media_type="text/event-stream",
            # Without this an intermediate proxy will happily buffer the whole
            # stream and hand the client one block at the end, which is the
            # exact behaviour streaming exists to avoid.
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    try:
        text = await run_in_threadpool(
            lambda: backend.chat_messages(
                messages=messages,
                temperature=req.temperature,
                max_tokens=limit,
            )
        )
    except RuntimeError as exc:
        # OffBackend and friends: a configuration problem, not a server fault.
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.warning("chat completion failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=502, detail=f"LLM request failed: {exc}") from exc

    return {
        "id": _completion_id(),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
    }


async def _stream(backend, model: str, messages: list[dict],
                  temperature: Optional[float], max_tokens: Optional[int]):
    """Relay the backend's blocking delta generator as SSE.

    The backend generator is synchronous, so it runs in a worker thread —
    iterating it on the event loop would stall every other request for the
    length of the completion.

    A failure mid-stream cannot become an HTTP error code: the 200 and the
    headers are long gone. It is emitted as a final content delta instead, so
    the client sees the reason in the text rather than a stream that simply
    stops.
    """
    cid = _completion_id()
    created = int(time.time())

    # OpenAI opens with a role-only delta; clients that build a message from
    # the stream rely on it to know the speaker.
    yield _chunk(cid, created, model, {"role": "assistant"})

    try:
        gen = backend.chat_messages_stream(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    except Exception as exc:
        logger.warning("chat stream refused: %s", exc)
        yield _chunk(cid, created, model, {"content": f"[error] {exc}"}, "stop")
        yield "data: [DONE]\n\n"
        return

    try:
        async for delta in iterate_in_threadpool(gen):
            yield _chunk(cid, created, model, {"content": delta})
    except Exception as exc:
        logger.warning("chat stream failed mid-flight: %s", exc, exc_info=True)
        yield _chunk(cid, created, model, {"content": f"[error] {exc}"})

    yield _chunk(cid, created, model, {}, "stop")
    yield "data: [DONE]\n\n"


# ── GET /v1/models ──────────────────────────────────────────────────────────


@router.get("/models")
async def list_models():
    """The single model this install serves.

    Clients populate their model picker from here. Advertising one entry is
    honest — there is exactly one configured LLM — and it means the value the
    user picks is the value that runs.
    """
    backend = _resolve_backend()
    model = _model_name(backend)
    return {
        "object": "list",
        "data": [
            {
                "id": model,
                "object": "model",
                "created": 0,
                "owned_by": "omnivoice",
            }
        ],
    }
