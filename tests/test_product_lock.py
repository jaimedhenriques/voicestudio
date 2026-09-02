"""Product lock: Voice Studio stays AGPL-3.0 and creator-TTS.

Smallest LICENSE-safe lock so a later change cannot drop the license,
merge this company into Whisp, add a store, or reframe the ICP as
dictation-first.
"""

from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "PLAN.md"

# Public copy we own in this increment. Upstream README / LICENSE-NOTICE
# retain original copyright holders; do not scrub those files here.
PUBLIC_COPY = (PLAN,)

# Featured personal names. "Fitch" is locked separately as a refused name.
FORBIDDEN_PERSONAL_NAMES = (
    "Jaime",
    "Henriques",
    "Palash",
    "Debnath",
)


def test_license_file_is_present_and_agpl_v3() -> None:
    license_text = (ROOT / "LICENSE").read_text()
    assert "GNU AFFERO GENERAL PUBLIC LICENSE" in license_text
    assert "Version 3, 19 November 2007" in license_text

    notice = (ROOT / "LICENSE-NOTICE.md").read_text()
    assert "AGPL-3.0-only" in notice

    package = json.loads((ROOT / "package.json").read_text())
    assert package["license"] == "AGPL-3.0-only"
    assert 'license = "AGPL-3.0-only"' in (ROOT / "pyproject.toml").read_text()


def test_plan_locks_creator_tts_positioning() -> None:
    assert PLAN.is_file()
    text = PLAN.read_text()
    lower = text.lower()

    assert "creators" in lower
    assert "text-to-speech" in lower
    assert "dictation" in lower
    assert "not people whose" in lower
    assert "elevenlabs" in lower
    assert "jaimedhenriques/ui" in lower
    assert "agpl-3.0-only" in lower
    assert "no store" in lower
    assert "must not be merged into whisp" in lower
    assert "independent" in lower
    assert "do not remove, relicense, or replace `license`" in lower


def test_plan_public_copy_has_no_personal_names() -> None:
    for path in PUBLIC_COPY:
        text = path.read_text()
        for name in FORBIDDEN_PERSONAL_NAMES:
            assert not re.search(rf"\b{re.escape(name)}\b", text), (
                f"{path.name} must not contain personal name {name!r}"
            )


def test_plan_refuses_fitch_and_store() -> None:
    text = PLAN.read_text()
    assert re.search(r"(?m)^- No Fitch\.", text)
    assert re.findall(r"\bFitch\b", text) == ["Fitch"]
    assert re.search(r"(?m)^- No store", text)
    assert "must not be merged into Whisp" in text
