# Agentic voice: VoiceStudio as a TTS/STT and LLM provider

VoiceStudio exposes an **OpenAI-compatible API**, so any agent framework that
speaks to OpenAI's audio endpoints can use your local VoiceStudio for speech —
in your own cloned voice, with nothing leaving your machine. You bring the
agent runtime; VoiceStudio is the voice. The same base URL also relays chat
completions to whichever LLM you have configured, so a tool that wants one
"custom OpenAI provider" for both speech and text can point at a single host.

This is "agentic v1": VoiceStudio is a provider, not the orchestrator. You wire
your own agent (a support line, a desk assistant, a Discord persona) and point
its TTS/STT at VoiceStudio.

> **Scope.** This page covers VoiceStudio-as-provider. Outbound phone calls are a
> separate, deferred milestone (they need a paid carrier — there is no
> fully-local path to the PSTN) and ship only behind explicit consent
> guardrails. See the roadmap in `docs/competitive-analysis.md` (§R1).

## The endpoints

VoiceStudio serves these on `http://localhost:3900/v1` (or your
[remote backend URL](remote-gpu.md)):

| OpenAI route | VoiceStudio support |
|---|---|
| `POST /v1/audio/speech` | TTS. `model` = engine id, `voice` = a voice-profile id (your clone) or preset, `response_format` incl. `pcm` and `wav`, `speed`. Default output is 24 kHz. |
| `POST /v1/audio/transcriptions` | STT (Whisper-family). |
| `GET /v1/audio/voices` | list available voices (VoiceStudio extension). |
| `POST /v1/chat/completions` | Chat, streaming or one-shot. A **relay**, not a model: forwarded to whichever LLM this install is already configured to use. |
| `GET /v1/models` | the one model that relay actually serves. |

Contract tests (`tests/test_agentic_provider_contract.py` for audio,
`tests/test_openai_chat_contract.py` for chat) pin these request shapes in CI,
so the recipes below won't silently break.

### About the chat relay

`POST /v1/chat/completions` owns no inference. Every request goes to the same
LLM adapter the dubbing translator, the glossary extractor, and the voice
agents use — configure the LLM once (`TRANSLATE_BASE_URL`, or Settings → LLM
Providers) and every consumer inherits it, including external ones.

Two deviations from OpenAI's API, both deliberate and both visible rather than
silent:

- **`model` in the request is advisory.** An install serves exactly one
  configured model, so the requested value is ignored and the response's
  `model` field reports what actually ran. You are never told you got a model
  you did not get.
- **Unknown request fields are accepted and ignored** (`reasoning_effort`,
  `enable_thinking`, `store`, …). Clients probe a new provider by sending their
  full parameter set; rejecting an unrecognised knob with a `422` would fail
  the connection test against a provider that works fine.

With no LLM configured the endpoint returns `503` naming the setting to change,
never a `200` with empty text — an empty completion is indistinguishable from a
successful one that deleted your content.

## pipecat (recommended)

[pipecat](https://github.com/pipecat-ai/pipecat) (BSD-2) runs as a Python
library inside your own process — no extra server. Point its OpenAI TTS/STT
services at VoiceStudio:

```python
from pipecat.services.openai.tts import OpenAITTSService
from pipecat.services.openai.stt import OpenAISTTService

tts = OpenAITTSService(
    base_url="http://localhost:3900/v1",
    api_key="not-needed-locally",        # any string; VoiceStudio ignores it unless OMNIVOICE_API_KEY is set
    voice="<your-voice-profile-id>",     # from GET /v1/audio/voices, or "default"
    model="omnivoice",                   # or any installed engine id
    sample_rate=24000,                   # matches VoiceStudio's default output
)

stt = OpenAISTTService(
    base_url="http://localhost:3900/v1",
    api_key="not-needed-locally",
)
```

Drop those into any pipecat pipeline (VAD, turn-taking, and LLM stay local
too). A minimal runnable example is in
[`examples/agentic/pipecat_minimal.py`](../examples/agentic/pipecat_minimal.py).

## LiveKit Agents

[LiveKit Agents](https://github.com/livekit/agents) (Apache-2.0) needs a
LiveKit media server alongside, but its OpenAI plugin takes the same
`base_url`:

```python
from livekit.plugins import openai

tts = openai.TTS(base_url="http://localhost:3900/v1", api_key="x", voice="<profile-id>")
stt = openai.STT(base_url="http://localhost:3900/v1", api_key="x")
```

Choose LiveKit over pipecat only when you need its WebRTC/SIP scale; for a
single local agent, pipecat is lighter.

## WhispVoice (macOS dictation)

[WhispVoice](https://github.com/jaimedhenriques/Whisp) (GPL-3.0) is a macOS
voice-to-text app: global-hotkey dictation into any app, on-device
transcription, and optional AI cleanup of the transcript afterwards.

Be precise about which half connects, because only one does:

| WhispVoice stage | Configurable endpoint? | What VoiceStudio can serve |
|---|---|---|
| **Transcription** | No. Every provider — Parakeet, Whisper, Apple Speech, FluidAudio, Nemotron — runs on-device, and there is no network ASR provider to point anywhere. | We serve `/v1/audio/transcriptions`, but WhispVoice has nothing to aim at it. Connecting these would mean adding a `TranscriptionProvider` on the WhispVoice side. |
| **AI enhancement** | Yes. Settings → AI Enhancement → add a custom provider with any base URL. | `POST /v1/chat/completions`. This works today, with no code change on either side. |

So the seam that exists right now is the enhancement one:

1. Configure an LLM in VoiceStudio (`TRANSLATE_BASE_URL`, or Settings → LLM
   Providers). The relay has no model of its own.
2. In WhispVoice: Settings → AI Enhancement → add a custom provider.
   - **Base URL** `http://localhost:3900/v1` — WhispVoice appends
     `/chat/completions` itself, so do not add it.
   - **API key** whatever `OMNIVOICE_API_KEY` is set to. Leave it blank if it
     is unset and you are on the same machine; loopback clients are not
     challenged.
   - **Model** the id from `GET /v1/models`.
3. Verify the connection. WhispVoice sends a one-token probe; a green result
   means the relay reached your LLM.

Dictated text now round-trips through your own LLM instead of a vendor's — the
transcript never leaves the machine when that LLM is local.

> Not the same thing as VoiceStudio's built-in dictation, which is already
> cross-platform and needs none of this. This is for people who want
> WhispVoice's macOS hotkey ergonomics on top.

## Remote backend

Running VoiceStudio on a [remote GPU box](remote-gpu.md)? Use that backend's URL
as `base_url` and pass its `OMNIVOICE_API_KEY` as the `api_key` — the same
bearer the rest of the app uses. Keep it on your tailnet, not the open
internet.

## Use your own voice responsibly

When an agent speaks in a cloned voice, prefer a profile you've marked
**verified own voice** (Settings → a voice profile → Voice ownership). That
consent lock is what gates the heavier agentic features as they land, and
it's the honest default for "an AI is speaking as me."
