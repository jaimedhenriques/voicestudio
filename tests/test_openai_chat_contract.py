"""Contract test for POST /v1/chat/completions — the chat half of the seam.

`openai_compat.py` makes this backend an OpenAI-compatible *audio* provider;
`openai_chat.py` adds chat, so any tool with a "custom OpenAI provider" setting
(WhispVoice's AI enhancement is the motivating one — see docs/agentic-voice.md)
can point its base URL at `http://localhost:3900/v1` and work with no code
change on either side.

"Works with no code change" is only true while the wire shape holds, and the
client is a separate repo that cannot fail our CI. So this test pins the parts
an external client actually depends on, taken from what those clients really
send and parse:

* the request shape they send, **including parameters we cannot honour** —
  a provider that 422s on an unknown knob fails their connection test outright;
* `choices[0].message.{role,content}` on the one-shot path, both non-optional
  in a typical Swift/TS decoder;
* `choices[0].delta.content` frames plus a terminal `data: [DONE]` on the
  streaming path;
* a configuration failure arriving as 503, not as a 200 with empty text — a
  client that gets "" silently enhances a transcript into nothing.

The LLM backend is stubbed throughout: this pins the HTTP contract, not the
model.
"""
import os

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

import json

import pytest


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    from main import app
    return TestClient(app, client=("127.0.0.1", 50000))


class _StubBackend:
    """Records what the router forwarded, returns what the test wants back."""

    id = "stub"
    display_name = "Stub LLM"
    model_name = "stub-model-v1"

    def __init__(self, reply="Enhanced transcript.", deltas=None):
        self._reply = reply
        self._deltas = deltas if deltas is not None else ["Enh", "anced", " text."]
        self.calls: list[dict] = []

    def chat_messages(self, *, messages, timeout=None, temperature=None, max_tokens=None):
        self.calls.append({
            "messages": messages, "temperature": temperature, "max_tokens": max_tokens,
        })
        return self._reply

    def chat_messages_stream(self, *, messages, timeout=None, temperature=None,
                             max_tokens=None):
        self.calls.append({
            "messages": messages, "temperature": temperature, "max_tokens": max_tokens,
            "stream": True,
        })
        yield from self._deltas


@pytest.fixture()
def stub(monkeypatch):
    backend = _StubBackend()
    import api.routers.openai_chat as mod
    monkeypatch.setattr(mod, "_resolve_backend", lambda: backend)
    return backend


def _sse_events(text: str) -> list[str]:
    """The payloads of `data:` lines, in order."""
    return [
        line[len("data: "):]
        for line in text.splitlines()
        if line.startswith("data: ")
    ]


# ── One-shot ────────────────────────────────────────────────────────────────


def test_the_request_a_custom_provider_client_sends_is_accepted(client, stub):
    """The literal body shape such clients send, unknown parameters included."""
    res = client.post("/v1/chat/completions", json={
        "model": "stub-model-v1",
        "messages": [{"role": "user", "content": "clean this up"}],
        "max_tokens": 50,
        # Sent by clients probing a provider's capabilities. Neither is
        # something we can honour — but 422-ing here would fail their
        # connection test against a provider that works.
        "reasoning_effort": "low",
        "enable_thinking": False,
    })
    assert res.status_code == 200, res.text
    body = res.json()

    choice = body["choices"][0]
    assert choice["message"]["role"] == "assistant"
    assert choice["message"]["content"] == "Enhanced transcript."
    assert choice["finish_reason"] == "stop"
    assert body["object"] == "chat.completion"
    assert body["id"].startswith("chatcmpl-")


def test_the_reported_model_is_the_one_that_actually_ran(client, stub):
    """A client asking for a model we do not have is told what it really got."""
    res = client.post("/v1/chat/completions", json={
        "model": "gpt-4o-not-installed-here",
        "messages": [{"role": "user", "content": "hi"}],
    })
    assert res.status_code == 200, res.text
    assert res.json()["model"] == "stub-model-v1"


def test_the_token_limit_reaches_the_backend_under_either_spelling(client, stub):
    """Ignoring max_tokens would return more than the client asked for."""
    client.post("/v1/chat/completions", json={
        "messages": [{"role": "user", "content": "hi"}], "max_tokens": 50,
    })
    assert stub.calls[-1]["max_tokens"] == 50

    # OpenAI's newer name for the same cap; reasoning-model clients send this.
    client.post("/v1/chat/completions", json={
        "messages": [{"role": "user", "content": "hi"}], "max_completion_tokens": 17,
    })
    assert stub.calls[-1]["max_tokens"] == 17


def test_messages_and_temperature_are_forwarded_verbatim(client, stub):
    client.post("/v1/chat/completions", json={
        "messages": [
            {"role": "system", "content": "You tidy dictation."},
            {"role": "user", "content": "um so like the thing"},
        ],
        "temperature": 0.2,
    })
    call = stub.calls[-1]
    assert call["temperature"] == pytest.approx(0.2)
    assert call["messages"] == [
        {"role": "system", "content": "You tidy dictation."},
        {"role": "user", "content": "um so like the thing"},
    ]


def test_an_empty_message_list_is_rejected(client, stub):
    res = client.post("/v1/chat/completions", json={"messages": []})
    assert res.status_code == 422


# ── Streaming ───────────────────────────────────────────────────────────────


def test_streaming_emits_delta_frames_and_terminates_with_done(client, stub):
    res = client.post("/v1/chat/completions", json={
        "messages": [{"role": "user", "content": "hi"}], "stream": True,
    })
    assert res.status_code == 200, res.text
    assert res.headers["content-type"].startswith("text/event-stream")

    events = _sse_events(res.text)
    assert events[-1] == "[DONE]", "clients stop reading on [DONE]; without it they hang"

    frames = [json.loads(e) for e in events[:-1]]
    assert all(f["object"] == "chat.completion.chunk" for f in frames)

    # The opening frame announces the speaker, as OpenAI's does.
    assert frames[0]["choices"][0]["delta"] == {"role": "assistant"}

    text = "".join(f["choices"][0]["delta"].get("content", "") for f in frames)
    assert text == "Enhanced text."

    assert frames[-1]["choices"][0]["finish_reason"] == "stop"


def test_streaming_proxy_buffering_is_disabled(client, stub):
    """A buffering proxy delivers the whole stream at once — i.e. not a stream."""
    res = client.post("/v1/chat/completions", json={
        "messages": [{"role": "user", "content": "hi"}], "stream": True,
    })
    assert res.headers.get("x-accel-buffering") == "no"
    assert "no-cache" in res.headers.get("cache-control", "")


def test_a_mid_stream_failure_is_reported_in_band(client, monkeypatch):
    """The 200 is already sent, so the reason has to ride the stream out."""
    class _Exploding(_StubBackend):
        def chat_messages_stream(self, **kw):
            yield "partial"
            raise RuntimeError("provider dropped the connection")

    import api.routers.openai_chat as mod
    monkeypatch.setattr(mod, "_resolve_backend", lambda: _Exploding())

    res = client.post("/v1/chat/completions", json={
        "messages": [{"role": "user", "content": "hi"}], "stream": True,
    })
    assert res.status_code == 200
    events = _sse_events(res.text)
    assert events[-1] == "[DONE]", "a failed stream must still terminate cleanly"
    text = "".join(
        json.loads(e)["choices"][0]["delta"].get("content", "") for e in events[:-1]
    )
    assert "partial" in text
    assert "provider dropped the connection" in text


# ── Failure modes ───────────────────────────────────────────────────────────


def test_no_configured_llm_is_a_503_naming_the_fix(client, monkeypatch):
    """A 200 with empty text would silently enhance a transcript into nothing."""
    class _Off:
        model_name = "none"

        def chat_messages(self, **kw):
            raise RuntimeError("No LLM backend configured. Set TRANSLATE_BASE_URL")

        def chat_messages_stream(self, **kw):
            raise RuntimeError("No LLM backend configured. Set TRANSLATE_BASE_URL")

    import api.routers.openai_chat as mod
    monkeypatch.setattr(mod, "_resolve_backend", lambda: _Off())

    res = client.post("/v1/chat/completions", json={
        "messages": [{"role": "user", "content": "hi"}],
    })
    assert res.status_code == 503
    assert "TRANSLATE_BASE_URL" in res.text, "the error must name the setting to change"


def test_a_refused_stream_still_terminates_cleanly(client, monkeypatch):
    """chat_messages_stream raises on *call*, before the first frame."""
    class _Off:
        model_name = "none"

        def chat_messages_stream(self, **kw):
            raise RuntimeError("No LLM backend configured. Set TRANSLATE_BASE_URL")

    import api.routers.openai_chat as mod
    monkeypatch.setattr(mod, "_resolve_backend", lambda: _Off())

    res = client.post("/v1/chat/completions", json={
        "messages": [{"role": "user", "content": "hi"}], "stream": True,
    })
    events = _sse_events(res.text)
    assert events[-1] == "[DONE]"
    assert "TRANSLATE_BASE_URL" in res.text


def test_an_upstream_failure_is_502_not_500(client, monkeypatch):
    """The upstream provider broke, not us — the status should say so."""
    class _Broken(_StubBackend):
        def chat_messages(self, **kw):
            raise ValueError("upstream returned 500")

    import api.routers.openai_chat as mod
    monkeypatch.setattr(mod, "_resolve_backend", lambda: _Broken())

    res = client.post("/v1/chat/completions", json={
        "messages": [{"role": "user", "content": "hi"}],
    })
    assert res.status_code == 502


# ── GET /v1/models ──────────────────────────────────────────────────────────


def test_models_advertises_exactly_what_is_served(client, stub):
    """Clients populate their picker from here; extra entries would be fiction."""
    res = client.get("/v1/models")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["object"] == "list"
    assert [m["id"] for m in body["data"]] == ["stub-model-v1"]
