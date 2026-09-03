# Voice Studio product plan

Voice Studio is an independent company and product. It is not Whispvoice
and must not be merged into Whisp.

## License (do not drop)

Keep the existing grant. This repository is licensed **AGPL-3.0-only**
(`LICENSE`, `LICENSE-NOTICE.md`, `package.json`, `pyproject.toml`).
Do not remove, relicense, or replace `LICENSE`.

## ICP

Primary customer: **creators who need text-to-speech**, not people whose
main job is dictation.

TTS is the wedge: voice cloning, voice design, long-form / audiobook, and
video dubbing at a quality bar that can compete with ElevenLabs-class
hosted studios. Dictation and ASR may exist as supporting tools. They are
not the homepage promise, the competitive frame, or the ICP.

## Competition

Compete ElevenLabs-class: local-first studio quality, control, and creator
workflow — not a thin wrapper around a hosted API.

## UX

Visual and interaction language comes from
[`jaimedhenriques/ui`](https://github.com/jaimedhenriques/ui).
Do not invent a parallel design system.

## Non-goals

- No store (no marketplace, no in-app store, no asset store).
- No Fitch.
- No personal names on public copy (this plan, marketing, product framing).
- No merge into Whispvoice.

## Increment 2026-09-03 — ElevenLabs-compatible API shim

- ICP: creators who need TTS inside existing tooling (n8n, video editors,
  podcast pipelines) that already speak the hosted ElevenLabs API shape.
- Pain: a local-first studio that does not speak that shape keeps creators
  paying per-character hosted prices or re-wiring their pipelines.
- Competitor frame: ElevenLabs-class hosted TTS. We implement protocol
  compatibility from public API documentation only; no proprietary code,
  assets, or branding. Public copy says "ElevenLabs API-compatible", never
  a clone claim.
- Scope: `POST /v1/text-to-speech/{voice_id}` and `GET /v1/voices` mapped
  onto the existing engine registry and voice profiles. Backend only; no UI
  string changes this increment.
- UX bar: a creator points an existing ElevenLabs integration at the local
  server, swaps the base URL, and gets audio back. No new screens.
- Test gate: new mocked-backend pytest coverage for both endpoints, plus the
  existing router smoke suite, green locally and in CI
  (`Tests (backend + frontend)`).
- Next increment: voice_settings (stability/similarity) mapped onto engine
  options where the active backend supports them.

## Increment 2026-09-03 (2) — voice_settings validation and speed pass-through

- ICP: same creator-TTS wedge; this slice deepens the ElevenLabs-compatible
  endpoint shipped in the previous increment.
- Pain: ElevenLabs clients always send voice_settings; silently dropping
  them produces wrong pacing (speed) for existing pipelines.
- Scope: validate the public voice_settings fields (stability,
  similarity_boost, style in 0-1, use_speaker_boost boolean, speed in the
  backend-supported 0.25-4.0) with 422 on invalid values, and pass
  voice_settings.speed through to the existing synthesis speed parameter.
  stability/similarity_boost/style/use_speaker_boost stay explicitly ignored
  — no engine here maps them — and the docs say so. Backend only.
- UX bar: a creator's existing integration that sets speed gets the pacing
  it asked for; invalid payloads get a clear 422, not silent acceptance.
- Test gate: mocked-backend pytest proves validation 422s and that speed 1.5
  reaches create_speech as speed=1.5; existing suites stay green.
- Next increment: run the official ElevenLabs Python SDK against the local
  server with a small real model loaded, and map stability/similarity if an
  engine exposes a genuine equivalent.

## Increment 2026-09-03 (3) — reject explicit null in voice_settings

- Pain: the documented strict contract leaked — explicit JSON null was
  accepted for every voice_settings field instead of returning 422.
- Scope: one validation rule (explicit null on any voice_settings field
  is a 422; omitted keys unchanged), regression tests, doc wording. This
  is the final checker finding from slice 2 (issue #8, PR #7).
- Test gate: null-on-each-field 422 cases plus the existing 63-test
  targeted set, green locally and in CI.
- Next increment: run the official ElevenLabs Python SDK against the
  local server with a small real model loaded.

## Increment 2026-09-03 (4) — official SDK end-to-end proof

- Pain: compatibility so far is proven against mocks only; a real
  ElevenLabs SDK payload has never run against the live server.
- Scope: scripts/verify_elevenlabs_sdk.py — official elevenlabs SDK
  pointed at the local server: voices list, text_to_speech.convert with
  voice_settings.speed, assert real audio bytes. Run for real against
  KittenTTS (small public CPU model). No dependency changes
  (uv run --with elevenlabs). Docs record the result.
- Test gate: the script itself is the evidence; existing suites stay
  green. Engine blocked: stop and report rather than mocking it away.
- Next increment: hosted-demo decision, or stability/similarity mapping
  if an engine grows a genuine equivalent.
