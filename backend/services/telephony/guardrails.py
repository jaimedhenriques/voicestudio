"""The six §R1 guardrails, enforced in code and testable without a carrier.

``docs/competitive-analysis.md`` §R1 wrote these for a BYO-carrier design, where
the user supplies credentials and therefore carries the liability. This fork
sells **platform-managed numbers**, so we are the carrier of record: FCC 24-17 /
TCPA exposure, 10DLC and STIR/SHAKEN registration, and ELVIS Act tool-provider
liability land on us. The gate inverts; the guardrails get stricter.

The guardrails:

1. **Disclosure preamble** — synthesised server-side and played as the first
   audio of every call. Never supplied by the agent, never skippable. Satisfies
   Texas SB 140's 30-second window, the FCC's direction under TCPA, and EU AI
   Act Art. 50(1) in one stroke.
2. **Consent-locked voice** — the profile must be flagged ``verified_own_voice``.
   Cloning someone's voice onto a phone call without that flag is the exact
   harm the ELVIS Act names.
3. **Watermark, always** — agentic audio is marked with ``force=True``, ignoring
   the user preference. See ``services/conversation_tts.py``.
4. **Allowlist + daily cap, no bulk dial** — a destination must already be on
   the allowlist, and there is no endpoint anywhere that accepts a list of
   numbers. Rate limiting is a policy; having no bulk surface is a property.
5. **Immutable log** — every attempt writes a row before a carrier is contacted,
   refusals included, and nothing deletes them.
6. **Jurisdiction notice** — ``docs/telephony.md``.

**What is enforced here and what is not.** Everything above is real code with
real tests. What does *not* exist yet is the carrier leg: no Twilio/Telnyx
account, no number inventory, no 10DLC registration, no KYC. Until those exist
:func:`placement_state` reports ``NOT_PROVISIONED`` and the route returns 501.
Shipping a live carrier path before customer KYC would make this robocall
infrastructure, which is precisely what the guardrails exist to prevent.
"""

from __future__ import annotations

import enum
import logging
import re
import time
import uuid
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("omnivoice.telephony")

#: Calls per day, per install. Deliberately small: this is a product for talking
#: to people, not for reaching lists of them.
DEFAULT_DAILY_CAP = 20

#: Spoken before anything else on every outbound call. Server-side and fixed —
#: an agent cannot alter, shorten, or skip it. `{agent}` is the only
#: substitution, so the callee learns who is calling as well as what.
DISCLOSURE_TEMPLATE = (
    "Hello. This is an AI voice assistant calling on behalf of {agent}. "
    "This call uses a synthetic voice and may be recorded."
)

#: Appended when recording is on. Two-party-consent states require the callee to
#: be told before recording, not after.
RECORDING_NOTICE = " Recording has started."


class PlacementState(str, enum.Enum):
    """Why a call can or cannot be placed right now."""

    #: No carrier account, number inventory, or KYC yet. The default, today.
    NOT_PROVISIONED = "NOT_PROVISIONED"
    #: Provisioned, but this attempt fails a guardrail.
    REFUSED = "REFUSED"
    #: Every guardrail passes.
    READY = "READY"


@dataclass(slots=True)
class Decision:
    """The outcome of checking one placement attempt."""

    state: PlacementState
    #: Machine-readable refusal reason, e.g. ``"not_on_allowlist"``. None on READY.
    reason: Optional[str] = None
    #: Human-readable, safe to show a user.
    detail: str = ""
    #: The exact preamble that will be spoken. Present even on refusal so the UI
    #: can show what a call *would* say.
    disclosure: str = ""

    @property
    def allowed(self) -> bool:
        return self.state is PlacementState.READY


# ── destination normalisation ────────────────────────────────────────────

_E164 = re.compile(r"^\+[1-9]\d{7,14}$")


def normalize_e164(raw: str) -> Optional[str]:
    """Normalise a dialled string to E.164, or return None if it cannot be.

    Pure-python on purpose: `phonenumbers` is not in the dependency tree and
    adding a dependency to validate a format this strict is not worth it. We
    only accept fully-qualified international numbers — refusing to guess a
    country code is a feature, because guessing wrong dials a stranger.
    """
    if not raw:
        return None
    # Strip the characters humans put in phone numbers; keep a leading +.
    cleaned = re.sub(r"[\s().\-]", "", raw.strip())
    if cleaned.startswith("00"):
        cleaned = "+" + cleaned[2:]
    if not cleaned.startswith("+"):
        return None
    return cleaned if _E164.match(cleaned) else None


def build_disclosure(agent_name: str, *, recorded: bool = False) -> str:
    """The exact words the call opens with. Never agent-authored."""
    text = DISCLOSURE_TEMPLATE.format(agent=agent_name.strip() or "an organisation")
    if recorded:
        text += RECORDING_NOTICE
    return text


# ── the checks ───────────────────────────────────────────────────────────


def is_provisioned() -> bool:
    """Whether a real carrier path exists. False until KYC and 10DLC are done.

    Intentionally not an env-var override. A flag that turns on live dialling
    is exactly the thing that should require a code change and a review.
    """
    return False


def calls_today(conn, *, now: Optional[float] = None) -> int:
    """Attempts that reached the placement endpoint since local midnight.

    Counts refusals too. A caller probing the allowlist a hundred times is
    exactly the pattern the cap exists to slow down, and not counting refusals
    would make the cap trivially evadable.
    """
    now = time.time() if now is None else now
    midnight = now - (now % 86400)
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM telephony_calls WHERE created_at >= ?",
        (midnight,),
    ).fetchone()
    return int(row["n"] if row else 0)


def is_allowlisted(conn, e164: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM telephony_allowlist WHERE e164 = ?", (e164,)
    ).fetchone()
    return row is not None


def profile_is_consent_locked(conn, voice_profile: Optional[str]) -> bool:
    """§R1 guardrail 2: the voice must be one the user proved is theirs."""
    if not voice_profile:
        return False
    row = conn.execute(
        "SELECT verified_own_voice FROM voice_profiles WHERE id = ?",
        (voice_profile,),
    ).fetchone()
    return bool(row and row["verified_own_voice"])


def check_placement(
    conn,
    *,
    agent_name: str,
    voice_profile: Optional[str],
    destination: str,
    recorded: bool = False,
    daily_cap: int = DEFAULT_DAILY_CAP,
    now: Optional[float] = None,
) -> Decision:
    """Evaluate every guardrail for one attempt. Never places a call.

    Order matters for the message the user sees: the checks run cheapest and
    most-fixable first, so someone with three problems is told about the
    typo in the number before being told about their voice profile.
    """
    disclosure = build_disclosure(agent_name, recorded=recorded)

    def refuse(reason: str, detail: str) -> Decision:
        return Decision(
            state=PlacementState.REFUSED,
            reason=reason,
            detail=detail,
            disclosure=disclosure,
        )

    e164 = normalize_e164(destination)
    if not e164:
        return refuse(
            "invalid_destination",
            "Enter a full international number, including the country code "
            "(for example +14155550123).",
        )

    if not is_allowlisted(conn, e164):
        return refuse(
            "not_on_allowlist",
            f"{e164} is not on the allowlist. Add it before calling — agents can "
            "only reach numbers that were added deliberately.",
        )

    if not profile_is_consent_locked(conn, voice_profile):
        return refuse(
            "voice_not_consented",
            "This agent's voice is not marked as a verified own voice. Confirm "
            "ownership on the voice profile before it can be used on a call.",
        )

    used = calls_today(conn, now=now)
    if used >= daily_cap:
        return refuse(
            "daily_cap_reached",
            f"The daily limit of {daily_cap} calls has been reached. It resets at "
            "midnight.",
        )

    if not is_provisioned():
        return Decision(
            state=PlacementState.NOT_PROVISIONED,
            reason="not_provisioned",
            detail=(
                "Outbound calling is not enabled on this install yet. Every "
                "guardrail is in place, but the carrier account, number "
                "inventory, and per-customer verification are not."
            ),
            disclosure=disclosure,
        )

    return Decision(state=PlacementState.READY, disclosure=disclosure)


# ── the immutable log ────────────────────────────────────────────────────


def log_attempt(
    conn,
    *,
    agent_id: Optional[str],
    destination: str,
    decision: Decision,
    recorded: bool = False,
    now: Optional[float] = None,
) -> str:
    """Write the attempt row. Called BEFORE any carrier is contacted.

    Returns the call id. Refusals are logged with ``status='refused'``: an
    audit trail that only contains successes is not an audit trail.
    """
    now = time.time() if now is None else now
    call_id = str(uuid.uuid4())
    status = "placing" if decision.allowed else "refused"
    conn.execute(
        """
        INSERT INTO telephony_calls
            (id, agent_id, destination, status, refused_reason,
             disclosure_text, recorded, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            call_id,
            agent_id,
            # Store what was dialled, normalised where possible — an
            # un-normalisable destination is still worth logging verbatim.
            normalize_e164(destination) or destination,
            status,
            decision.reason,
            decision.disclosure,
            1 if recorded else 0,
            now,
        ),
    )
    return call_id
