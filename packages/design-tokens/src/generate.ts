/**
 * Emits `dist/tokens.css` from `tokens.ts`.
 *
 * The CSS is committed rather than built on demand so that consumers — the
 * Vite app, the Astro site, and `deploy/Dockerfile`'s frontend stage — can
 * `@import` it with no build step and no cross-workspace ordering problem.
 * `tokens.test.ts` fails if the committed file drifts from the TS source, so
 * "committed generated artifact" cannot rot into "hand-edited artifact".
 *
 * Run: `bun run --cwd packages/design-tokens build`
 */

import { writeFileSync, mkdirSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import {
  fonts,
  layout,
  motion,
  neutral,
  radius,
  shadow,
  space,
  spectrum,
  themes,
  type,
} from './tokens.ts';

const HERE = dirname(fileURLToPath(import.meta.url));
export const OUT_PATH = resolve(HERE, '../dist/tokens.css');

const decl = (name: string, value: string | number) => `  --sn-${name}: ${value};`;

/** Theme-dependent declarations, shared by every selector that carries a theme. */
function themeBlock(theme: keyof typeof themes): string[] {
  const t = themes[theme];
  const lines: string[] = [];
  for (const [key, value] of Object.entries(t)) {
    if (key === 'scheme') continue;
    lines.push(decl(key, value));
  }
  lines.push(`  color-scheme: ${t.scheme};`);
  return lines;
}

/** Theme-independent declarations: the scales themselves. */
function scaleBlock(): string[] {
  const lines: string[] = [];

  lines.push('  /* Neutral ramp */');
  for (const [k, v] of Object.entries(neutral)) lines.push(decl(`neutral-${k}`, v));

  lines.push('', '  /* Spectrum — atmosphere only, never a control surface */');
  for (const [k, v] of Object.entries(spectrum)) lines.push(decl(`spectrum-${k}`, v));
  lines.push(
    decl(
      'spectrum-gradient',
      `linear-gradient(135deg, ${spectrum[1]} 0%, ${spectrum[2]} 32%, ${spectrum[3]} 58%, ${spectrum[4]} 84%, ${spectrum[5]} 100%)`,
    ),
  );

  lines.push('', '  /* Type */');
  for (const [k, v] of Object.entries(fonts)) lines.push(decl(`font-${k}`, v));
  for (const [k, v] of Object.entries(type)) {
    lines.push(decl(`text-${k}`, `${v.size / 16}rem`));
    lines.push(decl(`leading-${k}`, v.lh));
    lines.push(decl(`tracking-${k}`, v.tracking));
    lines.push(decl(`weight-${k}`, v.weight));
  }

  lines.push('', '  /* Space */');
  for (const [k, v] of Object.entries(space)) lines.push(decl(`space-${k}`, v));

  lines.push('', '  /* Radius */');
  for (const [k, v] of Object.entries(radius)) lines.push(decl(`radius-${k}`, v));

  lines.push('', '  /* Elevation */');
  for (const [k, v] of Object.entries(shadow)) lines.push(decl(`shadow-${k}`, v));

  lines.push('', '  /* Motion */');
  for (const [k, v] of Object.entries(motion)) lines.push(decl(k, v));

  lines.push('', '  /* Layout */');
  for (const [k, v] of Object.entries(layout)) lines.push(decl(k, v));

  return lines;
}

export function render(): string {
  return `/* GENERATED FILE — do not edit.
 * Source: packages/design-tokens/src/tokens.ts
 * Regenerate: bun run --cwd packages/design-tokens build
 * Guarded by: packages/design-tokens/src/tokens.test.ts
 */

/* Light is the default: the marketing site is light-first. The app opts into
 * dark explicitly via [data-sonari-theme="dark"], which its own
 * [data-theme="sonari"] block sets alongside. Consumers that want to follow
 * the OS instead get it from the prefers-color-scheme block below. */
:root {
${scaleBlock().join('\n')}

  /* Theme: light */
${themeBlock('light').join('\n')}
}

@media (prefers-color-scheme: dark) {
  :root:not([data-sonari-theme='light']) {
${themeBlock('dark')
  .map((l) => `  ${l}`)
  .join('\n')}
  }
}

:root[data-sonari-theme='dark'] {
${themeBlock('dark').join('\n')}
}

:root[data-sonari-theme='light'] {
${themeBlock('light').join('\n')}
}
`;
}

// Only write when executed directly, so the test can import `render()` cleanly.
if (import.meta.main) {
  mkdirSync(dirname(OUT_PATH), { recursive: true });
  writeFileSync(OUT_PATH, render());
  console.log(`wrote ${OUT_PATH}`);
}
