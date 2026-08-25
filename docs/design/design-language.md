# Design language

The visual system shared by the studio app and the marketing site. Tokens are in
`packages/design-tokens`; this page is the *why*, so nobody has to reverse-engineer intent
from hex values.

## The register

Editorial restraint. Warm-neutral greyscale, near-black ink, light display weights with
tight tracking, generous whitespace, hairlines instead of shadows. It should read like a
well-set print magazine that happens to be software — not like a SaaS template.

This is the register the category leaders work in, and it is a deliberate choice rather
than an imitation: a voice product's screen should get out of the way of the audio. The
one thing we borrow wholesale is the *discipline*. Everything below is ours.

## Five rules

**1. Ink is the only call to action.**
There is no primary blue. The one filled button in the system is near-black on light,
near-white on dark. Colour is reserved for atmosphere and for status. If a screen needs
two competing filled buttons, the screen is wrong, not the palette.

**2. Display type is light and tight.**
Every display step is weight 300 with negative tracking (`-0.01em` at 24px down to
`-0.03em` at 64px). Body runs 400–500 with *positive* tracking (`+0.01em`). That inversion
is the whole editorial signature — a bolded heading collapses the register instantly.
Enforced by `packages/design-tokens/src/tokens.test.ts`.

**3. Colour is atmosphere, never a surface.**
`--sn-spectrum-*` is a spectrogram ramp: violet-black → indigo → teal → amber → warm
light. It belongs in hero fields, waveform fills, and data-viz scales. It never fills a
button, a card, or a control. In the hero it sits at 10% opacity behind a radial mask so
body contrast is decided by `--sn-bg` alone.

**4. Elevation is a hairline, not a shadow.**
Two shadow tiers exist. `--sn-shadow-soft` is for cards that genuinely lift on hover;
`--sn-shadow-raised` is for things that actually float (menus, toasts). Everything else
separates with a 1px border and a background step.

**5. Dark mode is a first-class surface.**
Both themes derive from one neutral ramp read from opposite ends, so they cannot drift.
The marketing site is light-first (it is read once, in daylight, often on a phone); the
studio app is dark-first (it is lived in, at night, next to a waveform). A test asserts
the two themes declare identical key sets — a key present in one and missing from the
other silently inherits, which is the classic half-themed bug.

## Type

Three faces, all already self-hosted in the repo — no new dependency, no CDN request.

| Role | Face | Why |
|---|---|---|
| Display + UI | **Inter Variable** | Ships a genuine weight-300 master, so the light-display look is real rather than synthesised from a 400. |
| Data, latency figures, call logs | **IBM Plex Mono** | Already the app's mono; the agent product is technical and numbers should line up. |
| Long-form editorial | **Source Serif 4** | Reserved for genuine long-form moments. Not a decoration. |

Inter is a deliberate deviation from the `minimalist-ui` skill, which bans it as generic.
That ban assumes Inter-at-400 as a body default; Inter-at-300 as a *display* face at 48px
with `-0.025em` tracking is a different typeface in practice, and it costs nothing to
ship. Same reasoning for keeping Lucide icons: swapping the icon library across ~110
components is not a design improvement, it is a migration.

## Where the tokens live

| Surface | Mechanism |
|---|---|
| Source of truth | `packages/design-tokens/src/tokens.ts` |
| Generated CSS | `packages/design-tokens/dist/tokens.css` — committed; regenerate with `bun run build:tokens` |
| Marketing site | `@import '@sonari/design-tokens/tokens.css'` in `marketing/src/styles/global.css`, bridged into Tailwind v4 via `@theme inline` |
| Studio app | the `[data-theme="sonari"]` block in `frontend/src/index.css`, which aliases `--color-*` / `--chrome-*` onto `--sn-*` |

Every property is namespaced `--sn-*`, enforced by test. `frontend/src/index.css` is ~6,900
lines with a load-bearing cascade documented at the top of the file; an un-namespaced
`--color-*` arriving via `@import` would silently outrank its Tailwind `@theme` block.

## Working on the app's stylesheet

Read the comment at the top of `frontend/src/index.css` before touching it. The short
version:

- Theme blocks (`[data-theme=…]`) must stay **after** every default `:root`. `<html>` *is*
  `:root`, so both match the same element at equal specificity and only source order
  breaks the tie. A default `:root` placed after the themes silently kills every theme's
  chrome recolouring — this has already shipped once.
- Never fix that by raising specificity to `:root[data-theme=…]`. It stops matching the
  visual-regression harness, which sets `data-theme` on a wrapper div.
- Keep `@theme` and `@theme inline` adjacent. Tailwind v4 compiles them together and
  moving a block between them changes the generated CSS.

Guarded by `frontend/src/test/themeCascade.test.js` and `tokenParity.test.js`, plus the
Playwright visual suite. Update visual snapshots **once**, at the end of a change, and
look at every diff — updating them mid-change hides regressions.

## Writing

- Plain and specific. No "elevate", "seamless", "unleash", "next-generation", "game-changing".
- Numbers are the real ones. `docs/features.yaml` is the source; `docs-drift.yml` diffs it nightly.
- Say what a thing does, not how it should make someone feel.
- Sentence case everywhere except the `overline` step.
