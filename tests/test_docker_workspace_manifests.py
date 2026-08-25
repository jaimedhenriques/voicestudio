"""Every bun workspace must have its manifest COPY'd into the Docker frontend stage.

`deploy/Dockerfile` copies manifests, runs `bun install --frozen-lockfile`, and
only then copies sources. Bun resolves the *whole* workspace graph from the root
lockfile, so a member whose ``package.json`` is missing from the build context at
that layer is a hard error (``Workspace not found "<name>"``), not a skip.

``ci.yml`` cannot catch this even though it also runs ``--frozen-lockfile``: it
installs from a full git checkout, where every workspace's ``package.json`` is
already on disk. Docker sees only what has been ``COPY``'d so far. So the failure
is specific to the build context, and adding a workspace to the root
``package.json`` stays green in ``ci.yml`` and goes red only in ``docker.yml`` —
after the frontend job has already passed. This test closes that gap
deterministically, which is cheaper than another red-main incident.

Guards the class of bug, not the one instance: it derives the expected COPY set
from whatever ``workspaces`` currently says, including glob members, so a
workspace added tomorrow fails here on the same commit that adds it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "deploy/Dockerfile"
PACKAGE_JSON = ROOT / "package.json"


def _workspace_dirs() -> list[str]:
    """Resolve the root ``workspaces`` patterns to concrete directories.

    Mirrors bun's resolution closely enough for this guard: a literal entry is a
    directory; a trailing ``/*`` glob expands to each child holding a
    ``package.json``.
    """
    patterns = json.loads(PACKAGE_JSON.read_text())["workspaces"]
    dirs: list[str] = []
    for pattern in patterns:
        if pattern.endswith("/*"):
            parent = ROOT / pattern[: -len("/*")]
            dirs.extend(
                str(child.relative_to(ROOT))
                for child in sorted(parent.iterdir())
                if (child / "package.json").is_file()
            )
        else:
            dirs.append(pattern)
    return dirs


def _copied_manifest_dirs(dockerfile: str) -> set[str]:
    """Directories whose package.json the frontend stage copies before install.

    Only the region between ``FROM ... AS frontend-builder`` and the
    ``bun install`` line counts — a COPY after the install is too late to help.
    """
    start = dockerfile.index("AS frontend-builder")
    install = dockerfile.index("bun install --frozen-lockfile", start)
    region = dockerfile[start:install]

    copied: set[str] = set()
    for line in region.splitlines():
        line = line.strip()
        if not line.startswith("COPY "):
            continue
        # `COPY <src>... <dest>` — the destination is the last token.
        dest = line.split()[-1].rstrip("/")
        for src in line.split()[1:-1]:
            if not src.endswith("package.json"):
                continue
            if dest in (".", "./"):
                # `COPY package.json bun.lock ./` — the repo root, not a workspace.
                continue
            copied.add(dest.removeprefix("./"))
    return copied


def test_every_workspace_manifest_is_copied_before_frozen_install():
    dockerfile = DOCKERFILE.read_text()
    copied = _copied_manifest_dirs(dockerfile)
    missing = [d for d in _workspace_dirs() if d not in copied]
    assert not missing, (
        "deploy/Dockerfile does not COPY these workspaces' package.json before "
        f"`bun install --frozen-lockfile`: {missing}. Bun will fail with "
        '\'Workspace not found "<name>"\'. Add a line such as '
        f'`COPY {missing[0]}/package.json ./{missing[0]}/` to the frontend-builder '
        "stage. ci.yml will NOT catch this: it installs from a full checkout where "
        "every manifest is already on disk."
    )


def test_frontend_stage_still_uses_frozen_lockfile():
    """The frozen flag is what makes lockfile drift a build failure rather than a
    silent re-resolve. Losing it would make the guard above pointless."""
    dockerfile = DOCKERFILE.read_text()
    assert re.search(r"^RUN bun install --frozen-lockfile$", dockerfile, re.MULTILINE), (
        "deploy/Dockerfile's frontend stage must install with --frozen-lockfile "
        "so root bun.lock drift fails the build instead of silently re-resolving."
    )
