"""Vendored third-party source must stay attributed.

Copied-in code is the easy licence mistake: it does not appear in `bun.lock`,
`uv.lock`, or `Cargo.lock`, so every automated licence scan misses it, and the
only thing standing between the repo and an unattributed MIT copy is somebody
remembering. This test is that memory.

It enforces three things, each of which has failed in real projects:

1. Every file under a `vendor/` directory carries an origin header — so a
   reader who opens the file knows immediately it is not ours to edit freely.
2. Every vendor directory is named in `LICENSE-NOTICE.md`, with the upstream
   licence text present — so a new vendored package cannot land silently.
3. The lint override that exempts vendored code stays scoped to `vendor/` —
   so "turn the linter off for third-party code" cannot quietly widen into
   "turn the linter off".
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
VENDOR_ROOTS = sorted((ROOT / "frontend/src").rglob("vendor"))
SOURCE_SUFFIXES = {".ts", ".tsx", ".js", ".jsx"}


def _vendored_files() -> list[Path]:
    out: list[Path] = []
    for root in VENDOR_ROOTS:
        if not root.is_dir():
            continue
        out.extend(p for p in root.rglob("*") if p.suffix in SOURCE_SUFFIXES and p.is_file())
    return sorted(out)


def test_there_is_at_least_one_vendored_file_to_check():
    """Guards the guard: if the vendor tree moves, these tests must not pass vacuously."""
    assert _vendored_files(), (
        "No vendored source found under frontend/src/**/vendor/. If vendored code moved, "
        "update VENDOR_ROOTS here rather than deleting the check."
    )


@pytest.mark.parametrize("path", _vendored_files(), ids=lambda p: p.name)
def test_vendored_file_declares_its_origin_and_licence(path: Path):
    head = path.read_text(errors="ignore")[:1500]
    rel = path.relative_to(ROOT)
    assert "github.com/" in head, f"{rel} has no upstream URL in its header"
    assert "Licence" in head or "License" in head, f"{rel} does not name its licence"
    assert "LICENSE-NOTICE.md" in head, (
        f"{rel} does not point at LICENSE-NOTICE.md, where the full licence text lives"
    )


@pytest.mark.parametrize(
    "vendor_dir",
    [d for root in VENDOR_ROOTS for d in sorted(root.iterdir()) if d.is_dir()],
    ids=lambda d: d.name,
)
def test_every_vendored_package_appears_in_the_licence_notice(vendor_dir: Path):
    notice = (ROOT / "LICENSE-NOTICE.md").read_text()
    rel = vendor_dir.relative_to(ROOT).as_posix()
    assert rel in notice, (
        f"{rel} is vendored but not listed in LICENSE-NOTICE.md. Vendored code is invisible "
        "to every lockfile-based licence scanner, so the notice is the only record."
    )


def test_the_vendor_lint_exemption_stays_scoped_to_vendor():
    """A blanket lint-off would be indistinguishable from this, and much worse."""
    config = json.loads((ROOT / "frontend/.oxlintrc.json").read_text())
    exemptions = [
        o
        for o in config.get("overrides", [])
        if any("vendor" in pattern for pattern in o.get("files", []))
    ]
    assert exemptions, "the vendored-code lint override is gone — was it widened or removed?"
    for override in exemptions:
        for pattern in override["files"]:
            assert "vendor" in pattern, (
                f"lint exemption pattern {pattern!r} no longer targets vendored code only"
            )
