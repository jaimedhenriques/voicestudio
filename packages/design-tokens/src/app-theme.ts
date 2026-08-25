/**
 * The `[data-theme="sonari"]` block for the studio app's `index.css`.
 *
 * Why generate it instead of importing `dist/tokens.css` into the app:
 *
 *  - `frontend/src/index.css` is ~6,900 lines with a documented, load-bearing
 *    cascade. Every other theme block in it is written as colour literals, and
 *    matching that shape keeps the file readable and keeps
 *    `themeCascade.test.js` (which parses top-level blocks by source order)
 *    working exactly as before.
 *  - Importing the full token file would ship ~200 custom properties the app
 *    does not use, and would put an unlayered `:root` ahead of the app's own —
 *    survivable, but one more thing to reason about in a file that has already
 *    shipped one cascade regression.
 *
 * Generating gives the same drift-proofing without either cost:
 * `frontend/src/test/sonariTheme.test.js` asserts `index.css` contains exactly
 * the block this module renders, so the app's theme cannot drift from the
 * brand.
 *
 * The app is a dark studio surface, so this maps the DARK theme. The marketing
 * site is light-first; both come from the same ramp.
 */

import { themes, spectrum } from './tokens.ts';

const dark = themes.dark;

/**
 * App token name → value. Keys are the app's existing vocabulary
 * (`--color-*` / `--chrome-*`), so this is an alias list rather than a
 * translation. Ordered to mirror the neighbouring theme blocks in index.css.
 */
export const APP_THEME: Record<string, string> = {
  '--color-fg': dark.text,
  '--color-fg-muted': dark['text-muted'],
  '--color-fg-subtle': dark['text-subtle'],
  '--color-fg-inverse': dark['text-inverse'],

  '--color-bg': dark.bg,
  // The elevation steps are translucent in every existing theme so the app's
  // blurred panels read as glass over the canvas. Same values as the opaque
  // --sn-bg-elev-* ramp, carried at the alpha the app chrome expects.
  '--color-bg-elev-1': 'rgba(20, 20, 22, 0.85)',
  '--color-bg-elev-2': 'rgba(11, 11, 12, 0.40)',
  '--color-bg-elev-3': 'rgba(29, 29, 28, 0.30)',

  '--color-border': dark.border,
  '--color-border-strong': dark['border-strong'],
  '--color-border-warm': 'rgba(143, 142, 240, 0.12)',

  // Brand = the interactive accent. Ink stays the CTA fill; this is for focus
  // rings, links, and selected state.
  '--color-brand': dark.accent,
  '--color-brand-hover': dark['accent-hover'],
  '--color-brand-glow': 'rgba(143, 142, 240, 0.35)',

  // Accent = the warm end of the spectrum ramp, used for highlights and
  // waveform fills. Atmosphere, not a control surface.
  '--color-accent': spectrum[4],
  '--color-success': dark.success,
  '--color-warn': dark.warning,
  '--color-danger': dark.danger,
  '--color-info': '#6fb6cf',

  '--chrome-bg': '#141416',
  '--chrome-fg': dark.text,
  '--chrome-fg-muted': dark['text-muted'],
  '--chrome-fg-dim': '#4a4a47',
  '--chrome-border': '#26262a',
};

/** The caret SVG the app inlines per theme, stroked in the muted text colour. */
function selectCaret(stroke: string): string {
  const hex = stroke.replace('#', '%23');
  return (
    'url("data:image/svg+xml,%3Csvg%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22' +
    '%20width%3D%2210%22%20height%3D%226%22%3E%3Cpath%20d%3D%22M1%201l4%204%204-4%22%20' +
    `fill%3D%22none%22%20stroke%3D%22${hex}%22%20stroke-width%3D%221.5%22%20` +
    'stroke-linecap%3D%22round%22%20stroke-linejoin%3D%22round%22%2F%3E%3C%2Fsvg%3E")'
  );
}

/**
 * Render the exact block that must appear in `frontend/src/index.css`.
 *
 * MUST be placed AFTER every default `:root` and after the other
 * `[data-theme]` blocks — `<html>` is `:root`, so a plain `:root` and a
 * `[data-theme]` match the same element at equal specificity and only source
 * order breaks the tie. See the comment at the top of index.css.
 */
export function renderAppThemeBlock(): string {
  const width = Math.max(...Object.keys(APP_THEME).map((k) => k.length)) + 2;
  const lines = Object.entries(APP_THEME).map(
    ([name, value]) => `  ${`${name}:`.padEnd(width)}${value};`,
  );
  return [
    '[data-theme="sonari"] {',
    ...lines,
    '',
    '  /* Sonari — bg #0b0b0c is dark. */',
    '  color-scheme: dark;',
    `  --select-caret: ${selectCaret(dark['text-muted'])};`,
    '}',
  ].join('\n');
}
