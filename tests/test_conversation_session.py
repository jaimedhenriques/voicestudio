"""Conversation-loop behaviour, proven without a model, a GPU, or a socket.

``ConversationSession`` takes its LLM and TTS as injectable callables precisely
so the properties that matter — event ordering, incremental speech, barge-in,
and what ends up in history — can be asserted deterministically. A test that
needed a real model would be slow, flaky, and would not actually pin any of
these.
"""

from __future__ import annotations

import asyncio
import threading

import pytest

from services.conversation import (
    AgentConfig,
    ConversationSession,
    _split_complete,
)

AGENT = AgentConfig(
    id="a1",
    name="Test agent",
    system_prompt="You are terse.",
    voice_profile=None,
    language="en",
)


def _stub_llm(deltas, *, on_delta=None):
    """A synchronous delta generator, like the real client's stream."""

    def factory(*, messages, temperature=None):  # noqa: ARG001
        for d in deltas:
            if on_delta is not None:
                on_delta(d)
            yield d

    return factory


def _stub_tts(calls=None, sample_rate=24000):
    async def synth(text):
        if calls is not None:
            calls.append(text)
        # 2 bytes per sample; content is irrelevant, length proves it ran.
        return b"\x00\x01" * 8, sample_rate

    return synth


async def _drain(session, text):
    return [event async for event in session.take_turn(text)]


# ── sentence splitting ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "buffer,expect_out,expect_rest",
    [
        ("Hello there. How are", ["Hello there."], " How are"),
        ("One. Two! Three?", ["One.", "Two!", "Three?"], ""),
        # A decimal still arriving must not split — "3." looks like a sentence
        # end until the "5" lands.
        ("It costs 3.5 dollars", [], "It costs 3.5 dollars"),
        ("It costs 3.", [], "It costs 3."),
        # CJK terminators count.
        ("你好。再见。", ["你好。", "再见。"], ""),
        ("", [], ""),
    ],
)
def test_split_complete(buffer, expect_out, expect_rest):
    out, rest = _split_complete(buffer)
    assert out == expect_out
    assert rest == expect_rest


def test_split_complete_flushes_unpunctuated_run_on():
    """A model that never punctuates must not buffer silently forever."""
    long = "word " * 200
    out, rest = _split_complete(long)
    assert out, "an over-long unpunctuated buffer should flush a chunk"
    assert len(out[0]) <= 320
    assert len(rest) < len(long)


def test_split_complete_prefers_a_comma_when_flushing():
    head = "a" * 300
    buffer = f"{head}, and then some more text that keeps going"
    out, _ = _split_complete(buffer)
    assert out[0].endswith(","), "the forced flush should land on a comma, not mid-word"


# ── the turn ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_turn_emits_ordered_events_and_speaks_each_sentence():
    spoken = []
    session = ConversationSession(
        AGENT,
        llm_stream=_stub_llm(["Hello there. ", "How are you?"]),
        synthesize=_stub_tts(spoken),
    )

    events = await _drain(session, "hi")
    kinds = [e.kind for e in events]

    assert kinds[-1] == "done"
    assert kinds.count("done") == 1, "exactly one terminal event"
    # Every sentence is immediately followed by its own audio — the transport
    # relies on this pairing to keep playback in order.
    for i, kind in enumerate(kinds):
        if kind == "sentence":
            assert kinds[i + 1] == "audio", f"sentence at {i} not followed by audio"

    assert spoken == ["Hello there.", "How are you?"]


@pytest.mark.asyncio
async def test_first_sentence_is_spoken_before_the_stream_finishes():
    """The whole latency argument: speech starts on sentence 1, not on EOF.

    If this regresses the agent still works and every other test still passes —
    it just feels broken, because time-to-first-audio silently grows to include
    the entire completion.

    Asserted with a handshake rather than by counting deltas: the stub stream
    blocks after its first sentence and is only released by the synthesis call.
    If TTS were deferred until EOF, nothing would ever release it and the wait
    would time out. Counting deltas instead would be a race — the stub thread
    can outrun the consumer and produce a passing run by luck.
    """
    synthesis_started = threading.Event()
    released = []

    def blocking_stream(*, messages, temperature=None):  # noqa: ARG001
        yield "First sentence. "
        # Only reachable once something consumed the sentence above.
        released.append(synthesis_started.wait(timeout=5))
        yield "Second sentence."

    async def synth(text):
        synthesis_started.set()
        return b"\x00\x01", 24000

    session = ConversationSession(AGENT, llm_stream=blocking_stream, synthesize=synth)
    events = await _drain(session, "hi")

    assert released == [True], (
        "the first sentence was not synthesized until the LLM stream had already "
        "finished — TTS is no longer overlapping generation, so time-to-first-audio "
        "now includes the full completion"
    )
    assert events[-1].kind == "done"


@pytest.mark.asyncio
async def test_barge_in_stops_speaking_and_records_only_what_was_said():
    """Interrupting mid-turn must end the turn and truncate history honestly."""
    session = ConversationSession(
        AGENT,
        llm_stream=_stub_llm(["One. ", "Two. ", "Three. ", "Four. ", "Five."]),
        synthesize=_stub_tts(),
    )

    events = []
    async for event in session.take_turn("go"):
        events.append(event)
        # Interrupt as soon as the first sentence has been spoken.
        if event.kind == "audio":
            session.interrupt()

    kinds = [e.kind for e in events]
    assert kinds[-1] == "interrupted"
    assert "done" not in kinds

    said = [e.text for e in events if e.kind == "sentence"]
    assert said == ["One."], f"kept speaking after barge-in: {said}"

    # History must reflect what the user HEARD. Storing the full generation
    # would leave the agent referring back to sentences nobody heard.
    assistant = [m for m in session.history if m["role"] == "assistant"]
    assert assistant == [{"role": "assistant", "content": "One."}]


@pytest.mark.asyncio
async def test_barge_in_before_any_audio_drops_the_dangling_user_turn():
    """Interrupted before a single word was spoken: nothing to remember."""
    session = ConversationSession(
        AGENT,
        llm_stream=_stub_llm(["Hello", " there."]),
        synthesize=_stub_tts(),
    )

    events = []
    async for event in session.take_turn("hi"):
        events.append(event)
        # Interrupt on the very first delta, before any sentence completes.
        if event.kind == "token":
            session.interrupt()

    assert events[-1].kind == "interrupted"
    assert not any(e.kind == "audio" for e in events)
    # A user message with no reply would make the next prompt read as two
    # questions in a row.
    assert session.history == []


@pytest.mark.asyncio
async def test_llm_failure_surfaces_as_one_error_event():
    def exploding(*, messages, temperature=None):  # noqa: ARG001
        raise RuntimeError("provider exploded")
        yield  # pragma: no cover

    session = ConversationSession(AGENT, llm_stream=exploding, synthesize=_stub_tts())
    events = await _drain(session, "hi")

    assert [e.kind for e in events] == ["error"]
    assert "provider exploded" in events[0].text


@pytest.mark.asyncio
async def test_synthesis_failure_does_not_kill_the_turn():
    """One bad sentence should not silence the rest of the reply."""
    attempts = []

    async def flaky(text):
        attempts.append(text)
        if len(attempts) == 1:
            raise RuntimeError("engine hiccup")
        return b"\x00\x01", 24000

    session = ConversationSession(
        AGENT,
        llm_stream=_stub_llm(["One. ", "Two."]),
        synthesize=flaky,
    )
    events = await _drain(session, "hi")
    kinds = [e.kind for e in events]

    assert "error" in kinds
    assert kinds[-1] == "done", "the turn should continue past a single failed sentence"
    assert attempts == ["One.", "Two."]


# ── history ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_history_accumulates_and_is_trimmed_to_whole_turns():
    session = ConversationSession(
        AGENT,
        llm_stream=_stub_llm(["Ok."]),
        synthesize=_stub_tts(),
        history_turns=2,
    )
    for i in range(4):
        await _drain(session, f"question {i}")

    assert len(session.history) == 8  # untrimmed store; trimming happens at prompt time

    messages = session._messages("next")
    assert messages[0]["role"] == "system"
    # 2 turns = 4 messages, plus system and the new user message.
    assert len(messages) == 6
    # The window must OPEN on a user message, never on a stray assistant reply.
    assert messages[1]["role"] == "user"
    assert messages[-1] == {"role": "user", "content": "next"}


@pytest.mark.asyncio
async def test_system_prompt_is_always_first_and_never_duplicated():
    session = ConversationSession(
        AGENT, llm_stream=_stub_llm(["Ok."]), synthesize=_stub_tts()
    )
    await _drain(session, "hi")
    messages = session._messages("again")

    assert messages[0] == {"role": "system", "content": AGENT.system_prompt}
    assert sum(1 for m in messages if m["role"] == "system") == 1


@pytest.mark.asyncio
async def test_interrupt_is_cleared_between_turns():
    """A turn that was interrupted must not poison the next one."""
    session = ConversationSession(
        AGENT, llm_stream=_stub_llm(["One. ", "Two. ", "Three."]), synthesize=_stub_tts()
    )

    async for event in session.take_turn("first"):
        if event.kind == "audio":
            session.interrupt()
    assert session.interrupted, "the flag should still be set after the turn ends"

    # take_turn() clears it on entry, so the next turn runs to completion.
    events = await _drain(session, "second")
    assert events[-1].kind == "done", "the interrupt flag leaked into the next turn"


@pytest.mark.asyncio
async def test_concurrent_turns_do_not_deadlock_the_pump_thread():
    """Sanity: many short turns in sequence leave no thread wedged."""
    session = ConversationSession(
        AGENT, llm_stream=_stub_llm(["Ok."]), synthesize=_stub_tts()
    )
    await asyncio.gather(*(_drain(session, f"q{i}") for i in range(1)))
    for i in range(5):
        events = await _drain(session, f"q{i}")
        assert events[-1].kind == "done"
