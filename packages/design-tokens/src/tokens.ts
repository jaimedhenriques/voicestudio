/**
 * Sonari design tokens — the single source of truth for the brand.
 *
 * Consumed by two very different surfaces:
 *   - `marketing/`  — light-first editorial site.
 *   - `frontend/`   — dark studio app, via the `[data-theme="sonari"]` block
 *                     in `src/index.css`, which maps these onto the app's
 *                     existing `--color-*` / `--chrome-*` names.
 *
 * Every custom property emitted from here is namespaced `--sn-*` so it can
 * never collide with the app's own token scale, its Tailwind v4 `@theme`
 * block, or the `--chrome-*` legacy vars. See `generate.ts` for the CSS
 * emitter and `tokens.test.ts` for the drift guard that keeps
 * `dist/tokens.css` honest.
 *
 * Design register (documented in `docs/design/design-language.md`):
 * editorial restraint — warm-neutral greyscale, ink as the only CTA fill,
 * light display weights with tight tracking, one spectral gradient used as
 * atmosphere and never as an actionable surface.
 */

/**
 * The neutral ramp. Everything structural in both themes derives from this
 * one scale, which is why light and dark stay in step: dark mode is not a
 * separate palette, it is the same ramp read from the other end.
 *
 * Very slightly warm (the greens sit a touch above the blues) so large fields
 * of it read as paper rather than as screen grey.
 */
export const neutral = {
  0: '#ffffff',
  25: '#fbfbfa',
  50: '#f7f7f6',
  100: '#efefed',
  150: '#e6e6e3',
  200: '#dcdcd8',
  300: '#c7c7c2',
  400: '#a3a39d',
  500: '#7c7c76',
  600: '#5a5a55',
  700: '#3d3d3a',
  800: '#2a2a28',
  850: '#1d1d1c',
  900: '#141416',
  950: '#0b0b0c',
} as const;

/**
 * The signature: a spectrogram ramp, violet-black through indigo and teal to
 * amber. Used for atmosphere — hero fields, waveform fills, chart scales —
 * and never as a button or control surface. Ordered dark → light so it can be
 * fed straight to a `linear-gradient()` or a data-viz scale.
 */
export const spectrum = {
  1: '#1b1740',
  2: '#3a3a95',
  3: '#2f7f9b',
  4: '#d99b4a',
  5: '#f4e6c9',
} as const;

/** Interactive accent — focus rings, links, selected state. Ink stays the CTA. */
export const accent = {
  light: '#3a3a95',
  lightHover: '#2c2c78',
  dark: '#8f8ef0',
  darkHover: '#a5a4f4',
} as const;

export const semantic = {
  light: {
    success: '#157f4e',
    warning: '#9a6510',
    danger: '#b3372c',
  },
  dark: {
    success: '#4ec98a',
    warning: '#e0a84a',
    danger: '#f0837a',
  },
} as const;

/** Ink — the CTA fill and the strongest text weight. Cooler than the ramp. */
export const ink = '#18181a';

/**
 * Semantic colour, per theme. Key names are deliberately close to the app's
 * existing `--color-*` vocabulary so the `[data-theme="sonari"]` mapping in
 * `frontend/src/index.css` is a one-to-one alias list rather than a
 * translation exercise.
 */
export const themes = {
  light: {
    bg: neutral[50],
    'bg-elev-1': neutral[0],
    'bg-elev-2': neutral[25],
    'bg-elev-3': neutral[100],
    'bg-inset': neutral[100],

    text: ink,
    'text-muted': neutral[500],
    'text-subtle': neutral[400],
    'text-inverse': neutral[0],

    border: neutral[150],
    'border-strong': neutral[300],

    ink,
    'ink-hover': '#000000',
    'on-ink': neutral[0],

    accent: accent.light,
    'accent-hover': accent.lightHover,
    'accent-soft': 'rgba(58, 58, 149, 0.08)',

    success: semantic.light.success,
    warning: semantic.light.warning,
    danger: semantic.light.danger,

    'shadow-color': '0deg 0% 0%',
    scheme: 'light',
  },
  dark: {
    bg: neutral[950],
    'bg-elev-1': neutral[900],
    'bg-elev-2': neutral[850],
    'bg-elev-3': '#242422',
    'bg-inset': '#08080a',

    text: '#f2f2f0',
    'text-muted': '#9a9a94',
    'text-subtle': '#6e6e69',
    'text-inverse': neutral[950],

    border: 'rgba(242, 242, 240, 0.10)',
    'border-strong': 'rgba(242, 242, 240, 0.18)',

    // In dark mode the "ink" CTA inverts: a light fill on a dark field keeps
    // the single-CTA-colour rule intact without becoming invisible.
    ink: '#f2f2f0',
    'ink-hover': '#ffffff',
    'on-ink': neutral[950],

    accent: accent.dark,
    'accent-hover': accent.darkHover,
    'accent-soft': 'rgba(143, 142, 240, 0.14)',

    success: semantic.dark.success,
    warning: semantic.dark.warning,
    danger: semantic.dark.danger,

    'shadow-color': '0deg 0% 0%',
    scheme: 'dark',
  },
} as const;

/**
 * Type scale. Display steps run at weight 300 with negative tracking (the
 * editorial signature); body steps run 400–500 with slightly positive
 * tracking, which is what makes long-form copy read as set rather than as
 * rendered.
 *
 * `size` is px — the marketing site converts to rem at emit time; the app
 * keeps its own much denser scale and only borrows the display steps.
 */
export const type = {
  'display-mega': { size: 64, lh: 1.05, tracking: '-0.03em', weight: 300 },
  'display-xl': { size: 48, lh: 1.08, tracking: '-0.025em', weight: 300 },
  'display-lg': { size: 36, lh: 1.15, tracking: '-0.02em', weight: 300 },
  'display-md': { size: 30, lh: 1.2, tracking: '-0.015em', weight: 300 },
  'display-sm': { size: 24, lh: 1.25, tracking: '-0.01em', weight: 300 },
  'title-lg': { size: 20, lh: 1.35, tracking: '0', weight: 500 },
  'title-md': { size: 18, lh: 1.4, tracking: '0', weight: 500 },
  'title-sm': { size: 16, lh: 1.45, tracking: '0', weight: 600 },
  'body-lg': { size: 17, lh: 1.55, tracking: '0.01em', weight: 400 },
  body: { size: 15, lh: 1.55, tracking: '0.01em', weight: 400 },
  'body-sm': { size: 14, lh: 1.5, tracking: '0.01em', weight: 400 },
  caption: { size: 13, lh: 1.45, tracking: '0', weight: 400 },
  overline: { size: 11, lh: 1.4, tracking: '0.08em', weight: 600 },
  button: { size: 14, lh: 1, tracking: '0', weight: 500 },
} as const;

/**
 * Font stacks. Inter Variable, IBM Plex Mono, and Source Serif 4 are already
 * self-hosted in `frontend/package.json` via @fontsource — reusing them keeps
 * the marketing site on the same faces with no new dependency and no CDN
 * request, which the artifact/CSP rules would block anyway.
 *
 * Inter at weight 300 is the display face. That is a deliberate substitution
 * for the proprietary grotesques this register usually reaches for: Inter
 * Variable ships a genuine 300 master, so the light-display look is real
 * rather than synthesised.
 */
export const fonts = {
  sans: "'Inter Variable', Inter, ui-sans-serif, system-ui, -apple-system, 'Segoe UI', sans-serif",
  mono: "'IBM Plex Mono', ui-monospace, SFMono-Regular, Menlo, monospace",
  serif: "'Source Serif 4 Variable', 'Source Serif 4', ui-serif, Georgia, serif",
} as const;

/** 4px base. `24` is the card rhythm; `96` is the section rhythm. */
export const space = {
  0: '0px',
  1: '2px',
  2: '4px',
  3: '8px',
  4: '12px',
  5: '16px',
  6: '20px',
  7: '24px',
  8: '32px',
  9: '48px',
  10: '64px',
  11: '96px',
  12: '128px',
} as const;

export const radius = {
  none: '0px',
  xs: '4px',
  sm: '6px',
  md: '8px',
  lg: '12px',
  xl: '16px',
  '2xl': '24px',
  pill: '999px',
} as const;

/**
 * Two tiers only. The register is flat: elevation is communicated by hairlines
 * and background steps, not by shadow. `raised` exists for genuinely floating
 * surfaces (menus, toasts) and nothing else.
 */
export const shadow = {
  none: 'none',
  soft: '0 1px 2px rgba(0, 0, 0, 0.04), 0 4px 16px rgba(0, 0, 0, 0.04)',
  raised: '0 8px 32px rgba(0, 0, 0, 0.10), 0 2px 8px rgba(0, 0, 0, 0.06)',
} as const;

export const motion = {
  'duration-instant': '80ms',
  'duration-fast': '140ms',
  'duration-base': '220ms',
  'duration-slow': '400ms',
  'ease-standard': 'cubic-bezier(0.2, 0, 0, 1)',
  'ease-entrance': 'cubic-bezier(0, 0, 0, 1)',
  'ease-exit': 'cubic-bezier(0.3, 0, 1, 1)',
} as const;

/** Reading measure for long-form marketing copy. */
export const layout = {
  'measure-prose': '68ch',
  'measure-narrow': '48ch',
  'page-max': '1200px',
  'page-gutter': '24px',
} as const;

export type ThemeName = keyof typeof themes;
