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
        last_kwargs: dict | None = None

        def generate(self, text, **kw) -> torch.Tensor:
            self.calls += 1
            self.last_kwargs = kw
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


def test_voices_returns_installed_profiles_in_elevenlabs_shape(client, profile) -> None:
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


def test_text_to_speech_returns_audio_bytes(client, profile, monkeypatch) -> None:
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


def test_voice_settings_are_accepted_and_ignored(client, profile, monkeypatch) -> None:
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


def test_eleven_model_id_maps_to_the_active_engine(client, profile, active_engine) -> None:
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


def test_omitted_model_id_maps_to_the_active_engine(client, profile, active_engine) -> None:
    r = client.post(f"/v1/text-to-speech/{profile}", json={"text": "hello"})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("audio/")
    assert active_engine.calls == 1


def test_unknown_voice_id_is_404(client) -> None:
    r = client.post(
        "/v1/text-to-speech/no-such-voice-id", json={"text": "hello"}
    )
    assert r.status_code == 404, r.text
    assert "no-such-voice-id" in r.json()["detail"]


def test_empty_text_is_422(client, profile) -> None:
    """Validation runs before the handler, so an empty body is refused without
    ever reaching an engine — a 422, not a 400 from deep inside synthesis."""
    r = client.post(f"/v1/text-to-speech/{profile}", json={"text": ""})
    assert r.status_code == 422, r.text


def test_missing_text_is_422(client, profile) -> None:
    r = client.post(f"/v1/text-to-speech/{profile}", json={})
    assert r.status_code == 422, r.text


# ── voice_settings ──────────────────────────────────────────────────────────


def test_speed_in_voice_settings_reaches_synthesis(client, profile, active_engine) -> None:
    """`speed` is the one honoured knob — assert it lands in the engine call,
    not merely that the request was accepted."""
    r = client.post(
        f"/v1/text-to-speech/{profile}",
        json={"text": "hello", "voice_settings": {"speed": 1.5}},
    )
    assert r.status_code == 200, r.text
    assert active_engine.last_kwargs["speed"] == 1.5


def test_omitted_voice_settings_leaves_the_default_speed(client, profile, active_engine) -> None:
    """No speed given must mean SpeechRequest's own default, not a value this
    router invented — the default stays single-sourced."""
    r = client.post(f"/v1/text-to-speech/{profile}", json={"text": "hello"})
    assert r.status_code == 200, r.text
    assert active_engine.last_kwargs["speed"] == 1.0


def test_ignored_settings_do_not_reach_synthesis(client, profile, active_engine) -> None:
    """stability/similarity_boost/style/use_speaker_boost are accepted and
    ignored — they must not be smuggled into the engine kwargs."""
    r = client.post(
        f"/v1/text-to-speech/{profile}",
        json={
            "text": "hello",
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.75,
                "style": 0.3,
                "use_speaker_boost": True,
                "speed": 1.25,
            },
        },
    )
    assert r.status_code == 200, r.text
    kw = active_engine.last_kwargs
    assert kw["speed"] == 1.25
    for ignored in ("stability", "similarity_boost", "style", "use_speaker_boost"):
        assert ignored not in kw


@pytest.mark.parametrize(
    "settings",
    [
        pytest.param({"stability": 2.0}, id="stability-above-1"),
        pytest.param({"stability": -0.1}, id="stability-below-0"),
        pytest.param({"similarity_boost": 1.5}, id="similarity_boost-above-1"),
        pytest.param({"style": 1.5}, id="style-above-1"),
        pytest.param({"speed": 0.1}, id="speed-below-min"),
        pytest.param({"speed": 9.0}, id="speed-above-max"),
    ],
)
def test_out_of_range_voice_settings_are_422(client, profile, settings) -> None:
    r = client.post(
        f"/v1/text-to-speech/{profile}",
        json={"text": "hello", "voice_settings": settings},
    )
    assert r.status_code == 422, r.text


@pytest.mark.parametrize(
    "settings",
    [
        # Pydantic's lax mode coerces every one of these into a valid-looking
        # value (bool is an int subclass; "1.5" parses as a float), so a
        # mistyped client payload would be accepted as if it were real.
        pytest.param({"use_speaker_boost": 1}, id="use_speaker_boost-int"),
        pytest.param({"use_speaker_boost": 0}, id="use_speaker_boost-zero"),
        pytest.param({"use_speaker_boost": "true"}, id="use_speaker_boost-str"),
        pytest.param({"use_speaker_boost": "loud"}, id="use_speaker_boost-nonsense-str"),
        pytest.param({"stability": True}, id="stability-bool"),
        pytest.param({"similarity_boost": False}, id="similarity_boost-bool"),
        pytest.param({"style": True}, id="style-bool"),
        pytest.param({"speed": True}, id="speed-bool"),
        pytest.param({"speed": "1.5"}, id="speed-numeric-str"),
        pytest.param({"stability": "0.5"}, id="stability-numeric-str"),
    ],
)
def test_wrong_scalar_types_are_422_not_coerced(client, profile, settings) -> None:
    r = client.post(
        f"/v1/text-to-speech/{profile}",
        json={"text": "hello", "voice_settings": settings},
    )
    assert r.status_code == 422, r.text


@pytest.mark.parametrize(
    "settings, expected_speed",
    [
        # JSON ints are legitimate for these knobs — rejecting them (as
        # StrictFloat would) breaks real clients that send 0 or 1.
        pytest.param({"stability": 1}, 1.0, id="stability-int"),
        pytest.param({"similarity_boost": 0}, 1.0, id="similarity_boost-int-zero"),
        pytest.param({"speed": 2}, 2.0, id="speed-int"),
        pytest.param({"use_speaker_boost": True}, 1.0, id="use_speaker_boost-real-bool"),
    ],
)
def test_int_and_bool_literals_are_accepted(
    client, profile, active_engine, settings, expected_speed
) -> None:
    r = client.post(
        f"/v1/text-to-speech/{profile}",
        json={"text": "hello", "voice_settings": settings},
    )
    assert r.status_code == 200, r.text
    assert active_engine.last_kwargs["speed"] == expected_speed


@pytest.mark.parametrize(
    "field",
    ["stability", "similarity_boost", "style", "use_speaker_boost", "speed"],
)
def test_explicit_null_on_a_field_is_422(client, profile, field) -> None:
    """An omitted key means "no opinion"; `null` means the client sent a value
    and that value is wrong. Every field being Optional made pydantic read the
    two as identical, which contradicted the documented 422."""
    r = client.post(
        f"/v1/text-to-speech/{profile}",
        json={"text": "hello", "voice_settings": {field: None}},
    )
    assert r.status_code == 422, r.text


def test_empty_voice_settings_object_is_accepted(client, profile, active_engine) -> None:
    """`{}` mentions no field at all, so it is an omission, not a null."""
    r = client.post(
        f"/v1/text-to-speech/{profile}",
        json={"text": "hello", "voice_settings": {}},
    )
    assert r.status_code == 200, r.text
    assert active_engine.last_kwargs["speed"] == 1.0


def test_null_voice_settings_object_is_accepted(client, profile, active_engine) -> None:
    """A null WHOLE object stays valid: SDKs that serialize every field emit
    `"voice_settings": null` for "I am not sending settings", which is an
    omission the serializer made explicit — not a bad value inside a settings
    object the caller chose to send."""
    r = client.post(
        f"/v1/text-to-speech/{profile}",
        json={"text": "hello", "voice_settings": None},
    )
    assert r.status_code == 200, r.text
    assert active_engine.last_kwargs["speed"] == 1.0


def test_null_alongside_a_valid_field_is_still_422(client, profile) -> None:
    """One good field does not excuse a null sibling."""
    r = client.post(
        f"/v1/text-to-speech/{profile}",
        json={"text": "hello", "voice_settings": {"speed": 1.5, "stability": None}},
    )
    assert r.status_code == 422, r.text


def test_unknown_field_null_behaves_like_unknown_field_non_null(
    client, profile, active_engine
) -> None:
    """The null guard covers the five declared fields only.

    An unknown key is outside this endpoint's contract; whatever the model
    does with it (drop it, under pydantic's default `extra` policy) must not
    change just because its value happens to be null."""
    def post(value):
        return client.post(
            f"/v1/text-to-speech/{profile}",
            json={"text": "hello", "voice_settings": {"speed": 1.5, "unknown_knob": value}},
        )

    non_null, null = post("whatever"), post(None)
    assert non_null.status_code == null.status_code, (non_null.text, null.text)
    assert null.status_code == 200, null.text
    assert active_engine.last_kwargs["speed"] == 1.5
