"""Full-duplex conversation loop for voice agents.

One turn is: committed user text → streamed LLM completion → sentence chunks →
TTS per sentence → PCM out. The whole design serves one number: the gap between
the user finishing a sentence and hearing the first syllable back. Waiting for a
complete LLM response before starting synthesis puts the entire generation time
in front of that first syllable, which is the difference between a conversation
and a kiosk.

**This module knows nothing about WebSockets.** It takes text in and yields
events out, and its LLM and TTS dependencies are injectable. That is what makes
the barge-in and ordering guarantees testable without a browser, a socket, a
model, or a GPU — see ``tests/test_conversation_session.py``.

Barge-in is the other half. When the user starts talking over the agent,
:meth:`ConversationSession.interrupt` must stop the *whole* pipeline, not just
mute the speaker: the LLM stream closes (so an unheard turn stops costing
tokens), queued sentences are dropped, and the partial text is recorded as what
was actually said, so the agent's own history matches what the user heard.

Design notes that are easy to get wrong:

* The LLM client is synchronous, so its stream is pumped on a worker thread into
  an ``asyncio.Queue``. The thread checks the interrupt flag between chunks and
  exits; it is never joined from the event loop.
* TTS runs on the shared GPU pool via ``run_on_gpu_pool_guarded``, the same
  dispatch every other synthesis route uses, so an agent turn queues fairly
  against a dub or a batch job instead of contending for the device.
* Every synthesized frame goes through ``mark_synthetic(force=True)``. ``force``
  bypasses the user's ``watermark.invisible`` preference: agentic audio is
  watermarked unconditionally. That is guardrail 3 in
  ``docs/competitive-analysis.md`` §R1 and the EU AI Act Art. 50(2) marking
  obligation, and it is not a toggle.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass
from typing import AsyncIterator, Awaitable, Callable, Optional

logger = logging.getLogger("omnivoice.conversation")

# How many prior turns to replay to the model. Voice conversations are short and
# latency-sensitive; an unbounded history quietly grows the prompt until
# time-to-first-token doubles. Trimmed by turn count rather than tokens because
# the count is exact and free, and voice turns are short by nature.
DEFAULT_HISTORY_TURNS = 12

# Sentences longer than this are split at the last comma before the limit, so a
# model that produces one 400-character run-on still starts speaking promptly.
MAX_SENTENCE_CHARS = 320


@dataclass(slots=True)
class AgentConfig:
    """Everything a turn needs to know about who is speaking."""

    id: str
    name: str
    system_prompt: str
    #: Voice-profile id. For any agentic *call* this profile must be
    #: consent-locked (``verified_own_voice``); the browser test path does not
    #: require it, because nobody is being called.
    voice_profile: Optional[str] = None
    #: Spoken before the user says anything. Empty means the agent waits.
    first_message: str = ""
    language: str = "en"
    temperature: Optional[float] = None
    llm_model: Optional[str] = None


@dataclass(slots=True)
class TurnEvent:
    """One thing that happened during a turn.

    ``kind`` is one of:
      ``token``       — an LLM delta (``text``)
      ``sentence``    — a complete sentence, about to be synthesized (``text``)
      ``audio``       — PCM16 mono for the preceding sentence (``audio``, ``sample_rate``)
      ``interrupted`` — barge-in fired; ``text`` is what was actually spoken
      ``done``        — turn complete; ``text`` is the full assistant message
      ``error``       — ``text`` is a user-safe message
    """

    kind: str
    text: str = ""
    audio: Optional[bytes] = None
    sample_rate: int = 0


class ConversationSession:
    """One live conversation. Not thread-safe; drive it from a single task."""

    def __init__(
        self,
        agent: AgentConfig,
        *,
        llm_stream: Optional[Callable[..., object]] = None,
        synthesize: Optional[Callable[[str], Awaitable[tuple[bytes, int]]]] = None,
        history_turns: int = DEFAULT_HISTORY_TURNS,
    ) -> None:
        """``llm_stream`` and ``synthesize`` default to the real backends and
        are injectable so tests can drive the loop deterministically.

        ``llm_stream(messages=..., temperature=...)`` is a SYNCHRONOUS
        generator of text deltas (the OpenAI client is sync; it is pumped on a
        worker thread). ``synthesize(text)`` is an AWAITABLE returning
        ``(pcm16_bytes, sample_rate)`` — it dispatches through the async GPU
        pool.
        """
        self.agent = agent
        self.history: list[dict] = []
        self._history_turns = history_turns
        self._llm_stream = llm_stream
        self._synthesize = synthesize
        self._interrupt = asyncio.Event()

    # ── interruption ─────────────────────────────────────────────────────

    def interrupt(self) -> None:
        """Cut the current turn short. Safe to call when nothing is running."""
        self._interrupt.set()

    @property
    def interrupted(self) -> bool:
        return self._interrupt.is_set()

    # ── prompt assembly ──────────────────────────────────────────────────

    def _messages(self, user_text: str) -> list[dict]:
        # Trim by *turns* (a user/assistant pair), not by messages, so the
        # window never starts on an assistant reply with no question in front
        # of it — models handle that badly and it reads as the agent talking
        # to itself.
        keep = self._history_turns * 2
        recent = self.history[-keep:] if keep else []
        return [
            {"role": "system", "content": self.agent.system_prompt},
            *recent,
            {"role": "user", "content": user_text},
        ]

    # ── the turn ─────────────────────────────────────────────────────────

    async def take_turn(self, user_text: str) -> AsyncIterator[TurnEvent]:
        """Run one turn, yielding events as they happen.

        Ordering is a contract the transport depends on: every ``sentence`` is
        immediately followed by its own ``audio``, and the turn ends with
        exactly one terminal event (``done``, ``interrupted``, or ``error``).
        """
        self._interrupt.clear()
        self.history.append({"role": "user", "content": user_text})

        spoken: list[str] = []
        buffer = ""
        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()
        stop = threading.Event()

        def _pump() -> None:
            """Drain the synchronous LLM stream from a worker thread."""
            try:
                stream = self._resolve_llm_stream()(
                    messages=self._messages(user_text),
                    temperature=self.agent.temperature,
                )
                for delta in stream:
                    if stop.is_set():
                        # Closing the generator closes the HTTP stream, so an
                        # interrupted turn stops billing immediately.
                        break
                    loop.call_soon_threadsafe(queue.put_nowait, ("token", delta))
            except Exception as exc:  # noqa: BLE001 — surfaced as an event
                logger.warning("conversation: LLM stream failed", exc_info=True)
                loop.call_soon_threadsafe(queue.put_nowait, ("error", str(exc)))
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, ("eof", None))

        thread = threading.Thread(target=_pump, name="conv-llm", daemon=True)
        thread.start()

        try:
            while True:
                if self._interrupt.is_set():
                    stop.set()
                    yield TurnEvent(kind="interrupted", text="".join(spoken))
                    self._commit(spoken)
                    return

                try:
                    kind, payload = await asyncio.wait_for(queue.get(), timeout=0.05)
                except asyncio.TimeoutError:
                    # Nothing yet — loop back so the interrupt check above runs
                    # even while the model is still thinking. Without this poll,
                    # barge-in during a long first-token wait would not land
                    # until the model finally produced something.
                    continue

                if kind == "error":
                    stop.set()
                    yield TurnEvent(kind="error", text=_safe_error(payload))
                    self._commit(spoken)
                    return

                if kind == "eof":
                    break

                buffer += payload
                yield TurnEvent(kind="token", text=payload)

                # Speak each sentence the moment it is complete rather than
                # waiting for the full reply — this is the whole latency win.
                sentences, buffer = _split_complete(buffer)
                for sentence in sentences:
                    if self._interrupt.is_set():
                        break
                    async for event in self._speak(sentence):
                        yield event
                        if event.kind == "sentence":
                            spoken.append(event.text)

            # Whatever is left after EOF is the final (unterminated) sentence.
            tail = buffer.strip()
            if tail and not self._interrupt.is_set():
                async for event in self._speak(tail):
                    yield event
                    if event.kind == "sentence":
                        spoken.append(event.text)

            if self._interrupt.is_set():
                yield TurnEvent(kind="interrupted", text="".join(spoken))
            else:
                yield TurnEvent(kind="done", text="".join(spoken))
            self._commit(spoken)
        finally:
            stop.set()

    async def _speak(self, sentence: str) -> AsyncIterator[TurnEvent]:
        """Synthesize one sentence. Yields ``sentence`` then its ``audio``."""
        text = sentence.strip()
        if not text:
            return
        yield TurnEvent(kind="sentence", text=text)
        try:
            pcm, sample_rate = await self._resolve_synthesize()(text)
        except Exception as exc:  # noqa: BLE001
            logger.warning("conversation: synthesis failed", exc_info=True)
            yield TurnEvent(kind="error", text=_safe_error(exc))
            return
        if pcm and not self._interrupt.is_set():
            yield TurnEvent(kind="audio", audio=pcm, sample_rate=sample_rate)

    def _commit(self, spoken: list[str]) -> None:
        """Record what was actually said — not what was generated.

        On barge-in these differ, and storing the full generation would leave
        the agent believing it said things the user never heard, which it then
        refers back to.
        """
        text = "".join(spoken).strip()
        if text:
            self.history.append({"role": "assistant", "content": text})
        else:
            # Nothing was spoken, so the user turn never got a reply. Drop it
            # rather than leaving a dangling user message that would make the
            # next prompt look like two questions in a row.
            if self.history and self.history[-1]["role"] == "user":
                self.history.pop()

    # ── real backends (resolved lazily so imports stay cheap) ────────────

    def _resolve_llm_stream(self) -> Callable[..., object]:
        if self._llm_stream is not None:
            return self._llm_stream
        from services.llm_backend import get_active_llm_backend

        backend = get_active_llm_backend()
        return backend.chat_messages_stream

    def _resolve_synthesize(self) -> Callable[[str], Awaitable[tuple[bytes, int]]]:
        if self._synthesize is not None:
            return self._synthesize
        from services.conversation_tts import synthesize_for_agent

        return lambda text: synthesize_for_agent(
            text,
            voice_profile=self.agent.voice_profile,
            language=self.agent.language,
        )


# ── helpers ──────────────────────────────────────────────────────────────


def _safe_error(exc: object) -> str:
    """User-facing error text with any credential material removed.

    Provider errors routinely echo the request, and a misconfigured base_url can
    put an API key in the message. `scrub_text` is the same filter the engine
    status strings use.
    """
    from core.scrub import scrub_text

    return scrub_text(str(exc)) or "The agent could not complete that turn."


# Latin terminators need a following space to count; CJK ones do not, because
# CJK text does not put a space after 。 — requiring one would buffer an entire
# Chinese reply into a single chunk and destroy the latency win for those users.
_TERMINATORS_LATIN = ".!?"
_TERMINATORS_CJK = "。！？…"
_TERMINATORS = _TERMINATORS_LATIN + _TERMINATORS_CJK


def _split_complete(buffer: str) -> tuple[list[str], str]:
    """Split off every *complete* sentence, returning them and the remainder.

    Deliberately simpler than ``services.sentence_chunker``: that one is tuned
    for long-form scripts, where mis-splitting an abbreviation is a quality bug
    worth avoiding at the cost of buffering. Here, buffering IS the bug —
    holding a sentence back to be sure about "Dr." costs the user real silence.
    A split slightly early is inaudible; a split slightly late is not.
    """
    out: list[str] = []
    start = 0
    for i, ch in enumerate(buffer):
        if ch not in _TERMINATORS:
            continue

        at_end = i + 1 >= len(buffer)
        if ch in _TERMINATORS_LATIN:
            if at_end:
                # Ambiguous: this could be a sentence end, or a decimal whose
                # fractional part has not arrived yet ("It costs 3." → "3.5").
                # Only a digit in front makes it worth waiting one more delta;
                # holding back every sentence-final period would add latency to
                # the common case to protect the rare one.
                if buffer[i - 1 : i].isdigit():
                    continue
            elif not buffer[i + 1].isspace():
                # Mid-buffer, a terminator with no space after it is inside a
                # token: "3.5", "e.g.", a URL.
                continue

        candidate = buffer[start : i + 1].strip()
        if candidate:
            out.append(candidate)
        start = i + 1

    remainder = buffer[start:]

    # A model that never punctuates would otherwise buffer forever. Flush at a
    # comma before the hard limit so the break lands somewhere prosodically
    # plausible instead of mid-word.
    if len(remainder) > MAX_SENTENCE_CHARS:
        cut = remainder.rfind(",", 0, MAX_SENTENCE_CHARS)
        cut = cut + 1 if cut > 0 else MAX_SENTENCE_CHARS
        chunk = remainder[:cut].strip()
        if chunk:
            out.append(chunk)
        remainder = remainder[cut:]

    return out, remainder
