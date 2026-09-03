"""ElevenLabs API-compatible endpoints — GET /v1/voices, POST /v1/text-to-speech.

Protocol compatibility only (public API shape), delegating to openai_compat so
engine resolution, the routing gate, eviction, load budget, GPU admission and
watermarking stay one code path. These tests pin the wire contract: the voices
response shape, that a POST returns real audio bytes with an audio/* type, and
the two error cases a client has to be able to distinguish (unknown voice vs.
empty text).

Fully mocked: a registry-real fake engine (same harness shape as
tests/test_openai_speech_engine_cache.py) so no model is downloaded or loaded.
"""
from __future__ import annotations

import os
import uuid

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

import pytest


def _tts_mod():
    import importlib

    return importlib.import_module("services.tts_backend")


def _make_engine(tb, eid: str):
    """A registry-real fake engine that returns one second of silence."""
    import torch

    class _E(tb.TTSBackend):
        id = eid
        display_name = f"{eid} (test)"
        supports_cloning = True
        gpu_compat = ("cpu",)

        @property
        def sample_rate(self) -> int:
            return 24000

        @property
        def supported_languages(self) -> list[str]:
            return ["multi"]

        @classmethod
        def is_available(cls):
            return True, "ready"

        calls = 0

        def generate(self, text, **kw) -> torch.Tensor:
            self.calls += 1
            return torch.zeros(1, 24000)

    return _E


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    from main import app

    import core.db

    core.db.init_db()
    return TestClient(app, client=("127.0.0.1", 50000))


@pytest.fixture()
def profile():
    """Insert a voice profile and remove it again, so the row can't leak."""
    from core.db import db_conn

    pid = f"el-test-{uuid.uuid4().hex[:8]}"
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO voice_profiles (id, name, ref_audio_path, ref_text, "
            "language, created_at) VALUES (?, ?, '', '', 'Auto', 0)",
            (pid, "ElevenLabs Compat Test Voice"),
        )
    yield pid
    with db_conn() as conn:
        conn.execute("DELETE FROM voice_profiles WHERE id=?", (pid,))


@pytest.fixture()
def active_engine(monkeypatch):
    """Stand in for `get_active_tts_backend()`.

    The suite-wide `OMNIVOICE_MODEL=test` sentinel makes the real active engine
    resolve a checkpoint that deliberately does not exist, so the active-engine
    path can only be exercised hermetically by swapping the accessor itself —
    which is exactly the seam the route reaches through.
    """
    svc = _tts_mod()
    engine = _make_engine(svc, "fake-active")()
    monkeypatch.setattr(svc, "get_active_tts_backend", lambda: engine)
    return engine


# ── GET /v1/voices ──────────────────────────────────────────────────────────


def test_voices_returns_installed_profiles_in_elevenlabs_shape(client, profile):
    r = client.get("/v1/voices")
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"voices"}

    entry = next((v for v in body["voices"] if v["voice_id"] == profile), None)
    assert entry is not None, f"profile {profile} missing from {body['voices']}"
    # The full ElevenLabs voice object — an integration reads these keys by name,
    # so a dropped/renamed field is a wire break, not a cosmetic change.
    assert entry == {
        "voice_id": profile,
        "name": "ElevenLabs Compat Test Voice",
        "category": "cloned",
        "labels": {},
        "description": None,
        "preview_url": None,
    }


# ── POST /v1/text-to-speech/{voice_id} ──────────────────────────────────────


def test_text_to_speech_returns_audio_bytes(client, profile, monkeypatch):
    svc = _tts_mod()
    monkeypatch.setitem(
        svc._REGISTRY, "fake-eleven", _make_engine(svc, "fake-eleven")
    )

    r = client.post(
        f"/v1/text-to-speech/{profile}",
        json={"text": "hello", "model_id": "fake-eleven"},
    )
    assert r.status_code == 200, r.text
    # `mp3` degrades to wav where torchaudio has no ffmpeg backend, so assert
    # the family, not one container — otherwise the test is machine-dependent.
    assert r.headers["content-type"].startswith("audio/"), r.headers
    assert len(r.content) > 0


def test_voice_settings_are_accepted_and_ignored(client, profile, monkeypatch):
    """An ElevenLabs client always sends voice_settings; refusing it over a knob
    we cannot honour would fail the integration outright."""
    svc = _tts_mod()
    monkeypatch.setitem(
        svc._REGISTRY, "fake-eleven", _make_engine(svc, "fake-eleven")
    )

    r = client.post(
        f"/v1/text-to-speech/{profile}",
        json={
            "text": "hello",
            "model_id": "fake-eleven",
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
        },
    )
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("audio/")


def test_eleven_model_id_maps_to_the_active_engine(client, profile, active_engine):
    """The real drop-in path: an ElevenLabs client sends its own hosted model
    name, which we don't have. It must route to the active engine the way
    openai_compat's 'tts-1' alias does, not 400 the request."""
    r = client.post(
        f"/v1/text-to-speech/{profile}",
        json={"text": "hello", "model_id": "eleven_multilingual_v2"},
    )
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("audio/")
    assert len(r.content) > 0
    assert active_engine.calls == 1


def test_omitted_model_id_maps_to_the_active_engine(client, profile, active_engine):
    r = client.post(f"/v1/text-to-speech/{profile}", json={"text": "hello"})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("audio/")
    assert active_engine.calls == 1


def test_unknown_voice_id_is_404(client):
    r = client.post(
        "/v1/text-to-speech/no-such-voice-id", json={"text": "hello"}
    )
    assert r.status_code == 404, r.text
    assert "no-such-voice-id" in r.json()["detail"]


def test_empty_text_is_422(client, profile):
    """Validation runs before the handler, so an empty body is refused without
    ever reaching an engine — a 422, not a 400 from deep inside synthesis."""
    r = client.post(f"/v1/text-to-speech/{profile}", json={"text": ""})
    assert r.status_code == 422, r.text


def test_missing_text_is_422(client, profile):
    r = client.post(f"/v1/text-to-speech/{profile}", json={})
    assert r.status_code == 422, r.text
