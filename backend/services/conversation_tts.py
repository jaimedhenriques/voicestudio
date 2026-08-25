"""Synthesis for voice agents: one sentence in, PCM16 out, always watermarked.

Split out from :mod:`services.conversation` so the conversation loop stays
transport- and model-free and can be unit-tested with a stub. This module is the
part that touches the GPU pool, the voice-profile table, and the watermark
chokepoint.

The one thing that differs from every other synthesis route: the watermark is
applied with ``force=True``. Elsewhere marking honours the user's
``watermark.invisible`` preference; for agentic output it is unconditional.
That is §R1 guardrail 3 in ``docs/competitive-analysis.md`` and the EU AI Act
Art. 50(2) machine-readable marking obligation — an agent speaking in a cloned
voice is precisely the case the obligation exists for, so there is no toggle.
"""

from __future__ import annotations

import functools
import logging
import os
from typing import Optional

logger = logging.getLogger("omnivoice.conversation")


def resolve_voice_kwargs(voice_profile: Optional[str]) -> dict:
    """Map a voice-profile id onto generation kwargs.

    Locked audio wins over the raw reference: a locked profile has had its
    reference frozen deliberately, and silently generating from the unlocked
    original would change the voice under the user.

    An unknown id is passed through as ``voice``, which is how the engines
    address their own built-in presets — so a preset name works here too.

    NOTE: this precedence is currently also inlined in
    ``api/routers/tts_stream.py``, ``generation.py``, ``audiobook.py`` and
    ``openai_compat.py``. Those four should migrate to this helper; that is a
    mechanical change worth its own diff, where the before/after is legible,
    rather than a rider on the agent feature.
    """
    kw: dict = {}
    if not voice_profile:
        return kw

    from core.config import VOICES_DIR
    from core.db import db_conn

    try:
        with db_conn() as conn:
            row = conn.execute(
                "SELECT * FROM voice_profiles WHERE id=?", (voice_profile,)
            ).fetchone()
    except Exception:
        logger.warning("conversation_tts: voice lookup failed", exc_info=True)
        return {"voice": voice_profile}

    if not row:
        return {"voice": voice_profile}

    if row["is_locked"] and row["locked_audio_path"]:
        kw["ref_audio"] = os.path.join(VOICES_DIR, row["locked_audio_path"])
    elif row["ref_audio_path"]:
        kw["ref_audio"] = os.path.join(VOICES_DIR, row["ref_audio_path"])
    if row["ref_text"]:
        kw["ref_text"] = row["ref_text"]
    if row["instruct"]:
        kw["instruct"] = row["instruct"]
    return kw


async def synthesize_for_agent(
    text: str,
    *,
    voice_profile: Optional[str] = None,
    language: str = "en",
) -> tuple[bytes, int]:
    """Synthesize one sentence. Returns ``(pcm16_mono_bytes, sample_rate)``.

    Async because dispatch goes through ``run_on_gpu_pool_guarded`` — the same
    guarded path every other synthesis route uses, so an agent turn queues
    fairly against dubs and batch jobs, and a wedged generate trips the pool
    reset instead of starving the device.
    """
    import torch

    from services.audio_dsp import apply_mastering, normalize_audio
    from services.model_manager import generate_timeout_s, run_on_gpu_pool_guarded
    from services.text_normalization import normalize_for_tts
    from services.tts_backend import get_active_tts_backend
    from services.watermark import mark_synthetic

    backend = get_active_tts_backend()
    kw = resolve_voice_kwargs(voice_profile)
    if language:
        kw["language"] = language

    # Same engine-agnostic pre-pass the other routes run: strips junk, expands
    # numbers and abbreviations. The conversation loop has already split
    # sentences, so this runs on one sentence at a time.
    clean = normalize_for_tts(text, language)

    def _generate():
        wav = backend.generate(clean, **kw)
        sr = backend.sample_rate
        # Studio engines that master their own output opt out of the broadcast
        # chain, matching tts_stream and openai_compat.
        if not getattr(backend, "applies_own_mastering", False):
            wav = apply_mastering(wav, sample_rate=sr)
        wav = normalize_audio(wav, target_dBFS=-2.0)
        # force=True — agentic output is ALWAYS marked, no pref, no toggle.
        # See the module docstring.
        wav = mark_synthetic(wav, sr, context="conversation.agent", force=True)
        return wav, sr

    wav, sample_rate = await run_on_gpu_pool_guarded(
        functools.partial(_generate),
        what="agent TTS",
        timeout=generate_timeout_s(clean, engine=backend),
    )

    pcm = (wav * 32767).clamp(-32768, 32767).to(torch.int16)
    if pcm.ndim == 2:
        pcm = pcm[0]  # mono
    return pcm.numpy().tobytes(), sample_rate
