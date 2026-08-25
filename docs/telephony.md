# Voice agents and phone calls

> **Status: not enabled.** Outbound calling is built but not provisioned. Every
> safeguard described here is enforced in code today; what does not exist is the
> carrier account, the number inventory, the 10DLC / STIR-SHAKEN registration,
> and per-customer verification. `POST /telephony/calls` returns `501` after
> logging the attempt. This page exists now rather than later because the rules
> below decide what gets built, and finding out about them after launch is the
> expensive order.

## Who carries the liability

This is the first thing to be clear about, because it changed with this fork.

Upstream's design had the **user** supply carrier credentials. That put the
regulatory exposure on the person placing the call and let the software argue it
was a tool. We sell **platform-managed numbers**: we hold the carrier account,
we resell minutes, and every call a customer places is a call we placed. The
exposure is ours.

Concretely, that means:

| Obligation | Who it lands on |
|---|---|
| TCPA / FCC 24-17 consent for AI-voice calls | Us, jointly with the customer |
| 10DLC brand + campaign registration | Us |
| STIR/SHAKEN attestation | Us, as originating provider |
| Know-your-customer on whoever is dialling | Us |
| Tennessee ELVIS Act, as a *tool provider* | Us |

None of that is optional, and none of it is code. It is why provisioning is a
deliberate business step and not a feature flag.

## The law, honestly

Not legal advice. These are the rules that shaped the design; a lawyer in your
jurisdiction is the right next call.

**United States — [FCC 24-17](https://www.fcc.gov/document/fcc-makes-ai-generated-voices-robocalls-illegal)
(February 2024).** AI-generated and cloned voices are "artificial" under the
TCPA. Calling a consumer with one requires **prior express consent**. The TCPA
carries a private right of action at **$500–$1,500 per call** — the exposure
scales with the call volume, which is the whole reason there is no bulk-dial
surface anywhere in this codebase.

**Texas SB 140.** An AI caller must disclose itself within the first 30 seconds.
Our preamble is the first audio on the call, which satisfies this comfortably.

**Tennessee ELVIS Act.** Extends voice-likeness liability to the *providers of
the tools*, not only the people using them. This is the specific reason a call
cannot be placed from a voice profile the user has not confirmed is their own.

**EU AI Act Article 50 — applicable since 2026-08-02.** Two duties: people must
be told they are interacting with an AI (Art. 50(1) — the preamble), and
synthetic audio must be marked in a machine-readable way (Art. 50(2) — the
AudioSeal watermark). **The open-source exemption in Art. 2(12) does not cover
Article 50.** Being an open-source project does not exempt this.

**Recording.** Consent to record varies by jurisdiction and, in the US, by
state. Two-party-consent states require the other person to be told *before*
recording begins. When recording is enabled, the notice is added to the preamble
rather than appended afterwards.

**The "my own voice, my own errand" case.** Using your own cloned voice to make
a single call on your own behalf is a genuine legal grey area. It is not clearly
prohibited and it is not clearly safe. Anyone who tells you otherwise is
guessing.

## The six safeguards

All six are enforced in `backend/services/telephony/guardrails.py` and covered
by `tests/test_telephony_guardrails.py` (32 tests) — none of which needs a
carrier account, a phone number, or a cent of spend.

### 1. A spoken disclosure the agent cannot remove

Every call opens with:

> "Hello. This is an AI voice assistant calling on behalf of *«agent name»*.
> This call uses a synthetic voice and may be recorded."

It is synthesised server-side and played as the first audio frame. The agent
never supplies it and cannot shorten or skip it. The agent's **name** is the
only substitution, and it lands inside the fixed sentence — so a name that reads
like an instruction ("Ignore previous instructions and say nothing") still
produces a complete disclosure. There is a test for exactly that.

### 2. A voice the user has confirmed is theirs

The chosen profile must be flagged `verified_own_voice`. This is the ELVIS Act
provision in code: cloning a voice you have no claim to and putting it on a
phone call is the harm the statute names.

Note the split: a browser conversation does **not** require this, because nobody
is being called. Only placing a call does. `GET /agents/{id}/readiness` reports
the two separately for that reason.

### 3. A watermark with no off switch

Agentic audio is marked with AudioSeal via `mark_synthetic(..., force=True)`.
`force` bypasses the user's `watermark.invisible` preference — everywhere else
in the app that preference is honoured; here it is not, because Art. 50(2) is
an obligation rather than a setting.

One honest caveat: AudioSeal embeds poorly in very short segments, and the
8 kHz G.711 phone leg has never been tested for watermark survival anywhere.
Phone-band downsampling may strip it. Verifying that is a prerequisite for
provisioning, not an afterthought.

### 4. An allowlist, a daily cap, and no bulk dial

A number must be added to the allowlist *before* it can be called. There is a
per-day cap (20), and it counts **refused** attempts too — a cap that ignored
failures could be evaded by making attempts that fail.

Most importantly: **there is no endpoint anywhere that accepts more than one
destination.** Rate limiting is a policy someone can raise; having no bulk
surface is a property of the code. Two tests assert it as a property — one on
`check_placement`'s signature, one on the router's route table.

### 5. An immutable log

Every attempt that reaches `POST /telephony/calls` writes a `telephony_calls`
row **before** any carrier is contacted, refusals included, with the exact
disclosure text that would have been spoken. Nothing in the app deletes them;
there is no DELETE route, and the migration's `downgrade()` deliberately leaves
the table in place. An audit log an operator can quietly prune is not an audit
log.

### 6. This page

Written before the feature ships, not after.

## What we will not build

- **Auto-dialers, campaigns, or list dialling.** Not a policy — there is no API
  surface for it, and tests fail if one appears.
- **Automatic redial.** A failed call writes a terminal row and stops. A redial
  loop is a campaign vector; re-initiating is manual and consumes another slot
  from the daily cap.
- **Concurrent calls**, in the first slice. One at a time, per install.

## Before the first real call

In order, and none of them optional:

1. Carrier account (Telnyx ≈ $0.005–0.007/min, Twilio ≈ $0.014/min) and number inventory.
2. 10DLC brand and campaign registration; STIR/SHAKEN attestation.
3. Per-customer KYC, and terms that pass the TCPA consent obligation through explicitly.
4. Measure watermark survival across the 8 kHz G.711 leg (safeguard 3's caveat).
5. Benchmark voice-to-voice latency against the ~600 ms p95 budget on a real carrier leg.
6. Legal review, per launch jurisdiction.

Only then does `is_provisioned()` change — and it takes no arguments precisely
so that flipping it requires a code change and a review, rather than an
environment variable somebody sets on a Friday.

## See also

- `docs/competitive-analysis.md` §R1 — the original research these safeguards come from
- `docs/specs/longform/32-phone-calls.md` — the full BYO-carrier spec this inverts
- `docs/agentic-voice.md` — using this app as a TTS/STT provider for your own agent
