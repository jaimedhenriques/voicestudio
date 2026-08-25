"""Voice agents: CRUD, the conversation socket, and the telephony preflight.

Endpoints (admin-gated — loopback, or an authenticated server/hosted session):

    GET    /agents                    → list agents
    POST   /agents                    → create
    GET    /agents/{id}               → one agent
    PUT    /agents/{id}               → update (partial)
    DELETE /agents/{id}               → remove
    GET    /agents/{id}/readiness     → can this agent actually talk right now?
    WS     /ws/converse               → the live conversation

    GET    /telephony/allowlist       → allowed destinations
    POST   /telephony/allowlist       → add one destination
    DELETE /telephony/allowlist/{e164}
    POST   /telephony/preflight       → evaluate every guardrail, place nothing
    POST   /telephony/calls           → place a call (501 until provisioned)
    GET    /telephony/calls           → the immutable attempt log

The conversation loop itself lives in ``services/conversation.py`` and knows
nothing about HTTP — this module is the transport, and the split is what lets
the loop's ordering and barge-in guarantees be tested without a socket.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from api.dependencies import require_admin
from core.db import db_conn
from services.conversation import AgentConfig, ConversationSession
from services.telephony import guardrails as tg

logger = logging.getLogger("omnivoice.agents")
router = APIRouter(dependencies=[Depends(require_admin)])


# ── shapes ───────────────────────────────────────────────────────────────


class AgentIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    system_prompt: str = ""
    first_message: str = ""
    voice_profile: Optional[str] = None
    language: str = "en"
    llm_model: Optional[str] = None
    temperature: Optional[float] = None
    enabled: bool = True


class AgentPatch(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    system_prompt: Optional[str] = None
    first_message: Optional[str] = None
    voice_profile: Optional[str] = None
    language: Optional[str] = None
    llm_model: Optional[str] = None
    temperature: Optional[float] = None
    enabled: Optional[bool] = None


class AllowlistIn(BaseModel):
    destination: str
    label: str = ""


class PreflightIn(BaseModel):
    agent_id: str
    destination: str
    recorded: bool = False


def _row_to_agent(row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "system_prompt": row["system_prompt"],
        "first_message": row["first_message"],
        "voice_profile": row["voice_profile"],
        "language": row["language"],
        "llm_model": row["llm_model"],
        "temperature": row["temperature"],
        "enabled": bool(row["enabled"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _load_agent(conn, agent_id: str):
    row = conn.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="No agent with that id.")
    return row


# ── agent CRUD ───────────────────────────────────────────────────────────


@router.get("/agents")
def list_agents():
    with db_conn() as conn:
        rows = conn.execute("SELECT * FROM agents ORDER BY updated_at DESC").fetchall()
    return {"agents": [_row_to_agent(r) for r in rows]}


@router.post("/agents", status_code=201)
def create_agent(body: AgentIn):
    now = time.time()
    agent_id = str(uuid.uuid4())
    with db_conn() as conn:
        conn.execute(
            """
            INSERT INTO agents (id, name, system_prompt, first_message, voice_profile,
                                language, llm_model, temperature, enabled,
                                created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                agent_id, body.name, body.system_prompt, body.first_message,
                body.voice_profile, body.language, body.llm_model,
                body.temperature, 1 if body.enabled else 0, now, now,
            ),
        )
        return _row_to_agent(_load_agent(conn, agent_id))


@router.get("/agents/{agent_id}")
def get_agent(agent_id: str):
    with db_conn() as conn:
        return _row_to_agent(_load_agent(conn, agent_id))


@router.put("/agents/{agent_id}")
def update_agent(agent_id: str, body: AgentPatch):
    fields = body.model_dump(exclude_unset=True)
    with db_conn() as conn:
        _load_agent(conn, agent_id)
        if fields:
            if "enabled" in fields:
                fields["enabled"] = 1 if fields["enabled"] else 0
            fields["updated_at"] = time.time()
            assignments = ", ".join(f"{k} = ?" for k in fields)
            conn.execute(
                f"UPDATE agents SET {assignments} WHERE id = ?",
                (*fields.values(), agent_id),
            )
        return _row_to_agent(_load_agent(conn, agent_id))


@router.delete("/agents/{agent_id}", status_code=204)
def delete_agent(agent_id: str):
    with db_conn() as conn:
        _load_agent(conn, agent_id)
        conn.execute("DELETE FROM agents WHERE id = ?", (agent_id,))
    return None


@router.get("/agents/{agent_id}/readiness")
def agent_readiness(agent_id: str):
    """Whether this agent can hold a conversation right now, and if not, why.

    Checked up front so a user learns their LLM is unconfigured from a panel
    rather than from an agent that opens its mouth and says nothing.
    """
    with db_conn() as conn:
        row = _load_agent(conn, agent_id)
        voice_ok = True
        voice_detail = ""
        if row["voice_profile"]:
            profile = conn.execute(
                "SELECT id FROM voice_profiles WHERE id = ?", (row["voice_profile"],)
            ).fetchone()
            if profile is None:
                voice_ok = False
                voice_detail = "The selected voice profile no longer exists."
        consent_ok = tg.profile_is_consent_locked(conn, row["voice_profile"])

    from services.llm_backend import get_active_llm_backend

    backend = get_active_llm_backend()
    llm_ok, llm_detail = type(backend).is_available()

    return {
        "ready": bool(llm_ok and voice_ok),
        "llm": {"ok": bool(llm_ok), "detail": llm_detail, "backend": backend.id},
        "voice": {"ok": voice_ok, "detail": voice_detail},
        # Not part of `ready`: an agent is perfectly usable in the browser
        # without a consent-locked voice. It is only calls that require one.
        "callable": {
            "ok": consent_ok,
            "detail": (
                ""
                if consent_ok
                else "Calling requires a voice profile you have confirmed is your own."
            ),
        },
    }


# ── the conversation socket ──────────────────────────────────────────────


@router.websocket("/ws/converse")
async def converse(websocket: WebSocket):
    """Live conversation with an agent.

    Client → server (JSON):
        {"type": "start",    "agent_id": "..."}
        {"type": "user",     "text": "..."}     committed user turn (ASR final)
        {"type": "barge_in"}                    user started talking over the agent
        {"type": "end"}

    Server → client:
        {"type": "state",   "value": "idle|thinking|speaking"}
        {"type": "token",   "text": "..."}      streamed LLM delta
        {"type": "sentence","text": "..."}      about to be spoken
        {"type": "audio",   "sample_rate": N}   followed by ONE binary PCM16 frame
        {"type": "interrupted", "text": "..."}  what was actually said
        {"type": "done",    "text": "..."}
        {"type": "error",   "detail": "..."}

    Audio is announced by a JSON frame and then sent as a single binary frame,
    so the client always knows the sample rate of the bytes it is about to get
    without parsing a container.
    """
    await websocket.accept()
    session: Optional[ConversationSession] = None

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "detail": "Malformed frame."})
                continue

            kind = message.get("type")

            if kind == "start":
                session = _open_session(message.get("agent_id"))
                await websocket.send_json({"type": "state", "value": "idle"})
                first = session.agent.first_message.strip()
                if first:
                    # The greeting is spoken through the same path as any other
                    # sentence, so it is normalised, mastered and watermarked
                    # identically. A greeting that skipped the watermark would
                    # be an unmarked synthetic utterance.
                    await _stream_turn(websocket, session, first, greeting=True)
                continue

            if session is None:
                await websocket.send_json(
                    {"type": "error", "detail": "Send a `start` frame first."}
                )
                continue

            if kind == "barge_in":
                # Fire-and-forget: the running turn notices on its next poll.
                # Not awaited, because the point of barge-in is that it lands
                # while something else is in flight.
                session.interrupt()
                continue

            if kind == "user":
                text = (message.get("text") or "").strip()
                if not text:
                    continue
                await _stream_turn(websocket, session, text)
                continue

            if kind == "end":
                break

            await websocket.send_json(
                {"type": "error", "detail": f"Unknown frame type: {kind!r}"}
            )

    except WebSocketDisconnect:
        # The client hung up mid-turn. Interrupting stops the LLM stream so an
        # abandoned conversation does not keep generating tokens nobody hears.
        if session is not None:
            session.interrupt()
    except HTTPException as exc:
        try:
            await websocket.send_json({"type": "error", "detail": exc.detail})
        except Exception:
            logger.debug("converse: could not report error to a closed socket")
    except Exception:
        logger.exception("converse: unhandled error")
        try:
            await websocket.send_json(
                {"type": "error", "detail": "The conversation ended unexpectedly."}
            )
        except Exception:
            logger.debug("converse: could not report error to a closed socket")
    finally:
        if session is not None:
            session.interrupt()
        try:
            await websocket.close()
        except Exception:
            logger.debug("converse: socket already closed")


def _open_session(agent_id: Optional[str]) -> ConversationSession:
    if not agent_id:
        raise HTTPException(status_code=400, detail="`start` needs an agent_id.")
    with db_conn() as conn:
        row = _load_agent(conn, agent_id)
        if not row["enabled"]:
            raise HTTPException(status_code=409, detail="That agent is disabled.")
        agent = AgentConfig(
            id=row["id"],
            name=row["name"],
            system_prompt=row["system_prompt"],
            voice_profile=row["voice_profile"],
            first_message=row["first_message"],
            language=row["language"] or "en",
            temperature=row["temperature"],
            llm_model=row["llm_model"],
        )
    return ConversationSession(agent)


async def _stream_turn(
    websocket: WebSocket,
    session: ConversationSession,
    text: str,
    *,
    greeting: bool = False,
) -> None:
    """Relay one turn's events onto the socket, preserving their order."""
    await websocket.send_json({"type": "state", "value": "thinking"})
    speaking = False

    async for event in session.take_turn(text):
        if event.kind == "audio":
            if not speaking:
                await websocket.send_json({"type": "state", "value": "speaking"})
                speaking = True
            await websocket.send_json(
                {"type": "audio", "sample_rate": event.sample_rate}
            )
            await websocket.send_bytes(event.audio or b"")
        elif event.kind in ("token", "sentence"):
            await websocket.send_json({"type": event.kind, "text": event.text})
        elif event.kind == "error":
            await websocket.send_json({"type": "error", "detail": event.text})
        else:  # done | interrupted
            await websocket.send_json({"type": event.kind, "text": event.text})

    await websocket.send_json({"type": "state", "value": "idle"})

    if greeting:
        # The greeting is the agent speaking unprompted. take_turn() recorded a
        # user message to carry it; drop it so the transcript does not open with
        # words the user never said.
        session.history = [m for m in session.history if m["role"] != "user" or m["content"] != text]


# ── telephony: allowlist ─────────────────────────────────────────────────


@router.get("/telephony/allowlist")
def list_allowlist():
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT e164, label, created_at FROM telephony_allowlist ORDER BY created_at"
        ).fetchall()
    return {"destinations": [dict(r) for r in rows]}


@router.post("/telephony/allowlist", status_code=201)
def add_to_allowlist(body: AllowlistIn):
    e164 = tg.normalize_e164(body.destination)
    if not e164:
        raise HTTPException(
            status_code=400,
            detail=(
                "Enter a full international number including the country code "
                "(for example +14155550123)."
            ),
        )
    with db_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO telephony_allowlist (e164, label, created_at) "
            "VALUES (?, ?, ?)",
            (e164, body.label, time.time()),
        )
    return {"e164": e164, "label": body.label}


@router.delete("/telephony/allowlist/{e164}", status_code=204)
def remove_from_allowlist(e164: str):
    with db_conn() as conn:
        conn.execute("DELETE FROM telephony_allowlist WHERE e164 = ?", (e164,))
    return None


# ── telephony: preflight, placement, log ─────────────────────────────────


def _decide(body: PreflightIn):
    with db_conn() as conn:
        row = _load_agent(conn, body.agent_id)
        decision = tg.check_placement(
            conn,
            agent_name=row["name"],
            voice_profile=row["voice_profile"],
            destination=body.destination,
            recorded=body.recorded,
        )
        used = tg.calls_today(conn)
    return row, decision, used


@router.post("/telephony/preflight")
def preflight(body: PreflightIn):
    """Evaluate every guardrail without placing anything or writing a row.

    Read-only by design: a UI can poll this as the user types a number without
    polluting the immutable log, which stays a record of real attempts.
    """
    _row, decision, used = _decide(body)
    return {
        "ok": decision.allowed,
        "state": decision.state.value,
        "reason": decision.reason,
        "detail": decision.detail,
        "disclosure": decision.disclosure,
        "calls_today": used,
        "daily_cap": tg.DEFAULT_DAILY_CAP,
    }


@router.post("/telephony/calls")
def place_call(body: PreflightIn):
    """Place a call. Currently always 501 — see the module docstring.

    Guardrails are re-evaluated here rather than trusting a prior preflight:
    the allowlist, the cap, and the profile's consent flag can all change in
    the gap, and preflight is advisory.
    """
    row, decision, _used = _decide(body)

    with db_conn() as conn:
        call_id = tg.log_attempt(
            conn,
            agent_id=row["id"],
            destination=body.destination,
            decision=decision,
            recorded=body.recorded,
        )

    if decision.state is tg.PlacementState.REFUSED:
        raise HTTPException(
            status_code=422,
            detail={"reason": decision.reason, "message": decision.detail, "call_id": call_id},
        )

    # NOT_PROVISIONED — every guardrail passed, but there is no carrier.
    raise HTTPException(
        status_code=501,
        detail={
            "reason": decision.reason,
            "message": decision.detail,
            "call_id": call_id,
        },
    )


@router.get("/telephony/calls")
def call_log(limit: int = 100):
    """The immutable attempt log, newest first. There is no delete endpoint."""
    limit = max(1, min(int(limit), 500))
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM telephony_calls ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        used = tg.calls_today(conn)
    return {
        "calls": [dict(r) for r in rows],
        "calls_today": used,
        "daily_cap": tg.DEFAULT_DAILY_CAP,
        "provisioned": tg.is_provisioned(),
    }
