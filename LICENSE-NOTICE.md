# VoiceStudio — License Notice

## Abbreviation

AGPL-3.0-only

## Notice

Copyright 2024-present Palash Debnath and VoiceStudio contributors.

VoiceStudio is **free and open-source software, licensed under the GNU
Affero General Public License, Version 3 (AGPL-3.0)**. You are free to use,
copy, modify, and redistribute it — and that **includes commercial and internal
business use**: run the app, use its outputs commercially, sell the audio you
produce with it, provide professional/client services with it, and deploy it
within your organization.

Because this is the **Affero** GPL, one additional obligation applies: if you
modify VoiceStudio and make that modified version available to others over
a network, you must also offer those users the complete corresponding source
code of your modified version under these same AGPL-3.0 terms. See the full
text in [`LICENSE`](LICENSE).

A **commercial license is available** for organizations that want to embed
VoiceStudio in a closed-source or proprietary product or service without
the AGPL-3.0 copyleft obligations. Pricing tiers are coming soon; for inquiries
contact `VoiceStudio@palash.dev`.

(This Notice is a plain-language summary; the binding terms are the full GNU
AGPL-3.0 text in [`LICENSE`](LICENSE).)

### Scope

These terms cover the VoiceStudio application — the Tauri desktop shell
(`frontend/src-tauri/`), the React frontend (`frontend/src/`), the FastAPI
backend (`backend/`), and supporting build / packaging scripts (`scripts/`,
`Dockerfile`, `docker-compose.yml`, `.github/`).

The bundled `omnivoice/` Python package — the underlying TTS model by Han Zhu —
is **separately licensed under Apache License 2.0** by its upstream authors and
is not relicensed here. Apache License 2.0 is compatible with, and may be
combined under, the GNU AGPL-3.0. See `pyproject.toml`.

Third-party dependencies retain their own licenses. See `Cargo.lock`,
`bun.lock`, and `uv.lock` for the resolved set.

### Third-party components vendored into this repository

Some third-party source is copied into this tree rather than installed as a
dependency, so it does not appear in any lockfile. Each vendored file carries a
header naming its origin; the licences are reproduced here.

**ElevenLabs UI** — `frontend/src/components/ui/vendor/elevenlabs/`

Copied from <https://github.com/elevenlabs/ui> (`apps/www/registry/elevenlabs-ui/ui/`).
Vendored rather than installed because that project distributes through a
shadcn registry aimed at Next.js app-router projects, while this app is
Vite + React. The components are unmodified apart from a dropped `"use client"`
directive and two explicit type annotations, both marked `SONARI:` in-file.

```
MIT License

Copyright (c) 2025 Eleven Labs Inc.

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

MIT is compatible with, and may be combined under, the GNU AGPL-3.0.

### Reference

The full canonical text of the GNU Affero General Public License, Version 3 is
reproduced verbatim in [`LICENSE`](LICENSE). The authoritative copy lives at
<https://www.gnu.org/licenses/agpl-3.0.txt>.

> **Why this notice is a separate file:** `LICENSE` must contain the verbatim
> AGPL-3.0 text and nothing else, so GitHub's license detection (and the
> corporate license scanners that gate adoption) can identify it as
> `AGPL-3.0-only` rather than falling back to "Other" / `NOASSERTION`.
