import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { renderAppThemeBlock, APP_THEME } from '@sonari/design-tokens/app-theme';

/**
 * Brand-drift guard for the app's `[data-theme="sonari"]` block.
 *
 * The block is written as colour literals to match every other theme in this
 * file (and to keep `themeCascade.test.js`'s source-order parsing working), but
 * its values come from `packages/design-tokens` — the same source the marketing
 * site reads. This test is what makes "written as literals" safe: edit the
 * token package and forget to regenerate, and this fails.
 *
 * Regenerate with: bun run build:tokens && bun run sync:app-theme
 */

const CSS = readFileSync(resolve(process.cwd(), 'src/index.css'), 'utf8');

describe('sonari theme block', () => {
  it('matches the generated block from @sonari/design-tokens exactly', () => {
    expect(
      CSS,
      'index.css has drifted from packages/design-tokens/src/app-theme.ts — run `bun run sync:app-theme`',
    ).toContain(renderAppThemeBlock());
  });

  it('declares every token the other themes declare', () => {
    // A theme missing a token silently inherits the default :root value, which
    // is how a theme ends up 90% recoloured with one stray gruvbox pink.
    const blockOf = (name) => {
      const start = CSS.indexOf(`[data-theme="${name}"] {`);
      if (start === -1) throw new Error(`no [data-theme="${name}"] block`);
      return CSS.slice(start, CSS.indexOf('\n}', start));
    };
    const tokensIn = (body) => new Set([...body.matchAll(/(--[a-z0-9-]+)\s*:/gi)].map((m) => m[1]));

    const reference = tokensIn(blockOf('catppuccin'));
    const sonari = tokensIn(blockOf('sonari'));
    const missing = [...reference].filter((t) => !sonari.has(t));
    expect(missing, `sonari is missing tokens the other themes set: ${missing}`).toEqual([]);
  });

  it('is placed after every other [data-theme] block', () => {
    // Equal specificity on <html> means source order is the only tiebreaker.
    // Being last is also what makes sonari safe as the default.
    const positions = ['midnight', 'nord', 'solarized', 'rose-pine', 'catppuccin'].map((n) =>
      CSS.indexOf(`[data-theme="${n}"] {`),
    );
    const sonariAt = CSS.indexOf('[data-theme="sonari"] {');
    expect(sonariAt).toBeGreaterThan(Math.max(...positions));
  });

  it('keeps ink-only-CTA discipline: no saturated brand fill leaks into chrome', () => {
    // --chrome-bg is a large field. If it ever picks up the accent instead of a
    // neutral, the register is gone. Cheap to assert, easy to regress.
    expect(APP_THEME['--chrome-bg']).toMatch(/^#(0|1|2)/);
    expect(APP_THEME['--chrome-bg']).not.toBe(APP_THEME['--color-brand']);
  });
});
