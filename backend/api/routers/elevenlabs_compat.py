"""
ElevenLabs API-compatible TTS endpoints.

Protocol compatibility only, implemented from the public ElevenLabs API
documentation so that tooling which already speaks that request/response shape
(n8n nodes, video editors, podcast pipelines) can point its base URL at this
backend and get audio back. No proprietary code, assets, or branding — this is
an API-compatible shim, not a clone.

    GET  /v1/voices                    → installed voice clone profiles
    POST /v1/text-to-speech/{voice_id} → text → audio bytes

Both endpoints delegate to ``openai_compat``: the voice profiles come from the
same ``voice_profiles`` table, and synthesis calls ``openai_compat.create_speech``
directly, so engine resolution, the routing gate, single-engine-resident
eviction, the model-load budget, GPU admission control and invisible provenance
watermarking are the exact same code path — not a second copy that can drift.

Of ElevenLabs' ``voice_settings``, only ``speed`` is honoured — it maps onto the
synthesis speed parameter. ``stability``, ``similarity_boost``, ``style`` and
``use_speaker_boost`` are validated and then ignored, because no engine here
exposes an equivalent; a request is never refused over a knob we cannot honour.

Auth is the app-wide ``BearerKeyMiddleware``, identical to ``openai_compat`` and
``openai_chat``: nothing here adds or bypasses a check.

Reference: https://elevenlabs.io/docs/api-reference/text-to-speech
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

router = APIRouter(prefix="/v1", tags=["ElevenLabs-Compatible API"])


# ── Schemas ─────────────────────────────────────────────────────────────────


class VoiceSettings(BaseModel):
    """ElevenLabs' `voice_settings` object.

    Every field is optional and validated, so an out-of-range value is refused
    with a 422 rather than silently clamped. Only `speed` is honoured — see the
    field descriptions for what each one does here.
    """

    stability: Optional[float] = Field(
        default=None, ge=0.0, le=1.0,
        description="Accepted and ignored — no engine here exposes an equivalent.",
    )
    similarity_boost: Optional[float] = Field(
        default=None, ge=0.0, le=1.0,
        description="Accepted and ignored — no engine here exposes an equivalent.",
    )
    style: Optional[float] = Field(
        default=None, ge=0.0, le=1.0,
        description="Accepted and ignored — no engine here exposes an equivalent.",
    )
    use_speaker_boost: Optional[StrictBool] = Field(
        default=None,
        description="Accepted and ignored — no engine here exposes an equivalent.",
    )
    speed: Optional[float] = Field(
        default=None, ge=0.25, le=4.0,
        description=(
            "Honoured: forwarded to the synthesis speed parameter. The accepted "
            "range is this backend's 0.25–4.0, a superset of ElevenLabs' 0.7–1.2."
        ),
    )

    @field_validator("stability", "similarity_boost", "style", "speed", mode="before")
    @classmethod
    def _numbers_only(cls, v: Any) -> Any:
        """Refuse JSON scalars that only *look* like numbers.

        Pydantic's lax mode coerces `true` → 1.0 (bool is an int subclass) and
        `"1.5"` → 1.5, so a mistyped payload would be accepted as a real value
        instead of reported. Plain ints stay valid — clients do send 0 and 1
        for these knobs — which is why this is a before-validator and not
        StrictFloat.
        """
        if isinstance(v, (bool, str)):
            raise ValueError("must be a number, not a boolean or string")
        return v


class TextToSpeechRequest(BaseModel):
    """POST /v1/text-to-speech/{voice_id} — mirrors ElevenLabs' request body."""

    # `model_id` collides with pydantic v2's protected `model_` namespace.
    model_config = ConfigDict(protected_namespaces=())

    text: str = Field(
        ...,
        min_length=1,
        max_length=4096,
        description="The text to synthesize. Max 4096 characters.",
    )
    model_id: Optional[str] = Field(
        default=None,
        description=(
            "Optional. ElevenLabs model names ('eleven_*') map to the active "
            "engine, as do VoiceStudio engine IDs (omnivoice, voxcpm2, …)."
        ),
    )
    voice_settings: Optional[VoiceSettings] = Field(
        default=None,
        description=(
            "Optional. `speed` is honoured: forwarded to the synthesis speed "
            "parameter, JSON numbers only (no booleans or strings), range "
            "0.25–4.0. `stability`, `similarity_boost` and `style` are JSON "
            "numbers only (no booleans or strings), range 0–1, then ignored. "
            "`use_speaker_boost` is JSON booleans only (no 0/1, no \"true\"), "
            "then ignored. The four ignored fields have no equivalent in any "
            "engine here, but a request is never refused over a knob we cannot "
            "honour. A wrong type or an out-of-range value is a 422, never a "
            "silent coercion or clamp."
        ),
    )


# ── Voices: GET /v1/voices ──────────────────────────────────────────────────


@router.get("/voices")
def list_voices() -> dict[str, list[dict[str, Any]]]:
    """List installed voice clone profiles in ElevenLabs' response shape."""
    from core.db import db_conn

    with db_conn() as conn:
        rows = conn.execute(
            "SELECT id, name FROM voice_profiles ORDER BY name"
        ).fetchall()

    return {
        "voices": [
            {
                "voice_id": row["id"],
                "name": row["name"],
                "category": "cloned",
                "labels": {},
                "description": None,
                "preview_url": None,
            }
            for row in rows
        ]
    }


# ── TTS: POST /v1/text-to-speech/{voice_id} ─────────────────────────────────


@router.post("/text-to-speech/{voice_id}")
async def text_to_speech(voice_id: str, req: TextToSpeechRequest) -> Response:
    """Synthesize `text` with an installed voice profile. Returns audio bytes."""
    from api.routers.openai_compat import SpeechRequest, create_speech
    from core.db import db_conn

    # 404 only when the profile genuinely isn't there — a DB failure must not
    # be reported as "unknown voice".
    with db_conn() as conn:
        row = conn.execute(
            "SELECT id FROM voice_profiles WHERE id=?", (voice_id,)
        ).fetchone()
    if row is None:
        raise HTTPException(
            status_code=404, detail=f"Voice '{voice_id}' not found."
        )

    # ElevenLabs model names name their hosted models, which we don't have.
    # Treat them the way openai_compat treats 'tts-1': pass through to the
    # active engine, rather than 400-ing every real ElevenLabs client.
    model = req.model_id
    if not model or model.startswith("eleven"):
        model = "tts-1"

    # Only `speed` crosses over. Omitting it leaves SpeechRequest's own default
    # as the single source of truth for what "no speed given" means.
    speed = req.voice_settings.speed if req.voice_settings else None
    extra = {} if speed is None else {"speed": speed}

    return await create_speech(
        SpeechRequest(
            model=model,
            input=req.text,
            voice=voice_id,
            response_format="mp3",
            **extra,
        )
    )
