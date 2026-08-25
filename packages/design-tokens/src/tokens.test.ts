import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';

import { render, OUT_PATH } from './generate.ts';
import { themes, spectrum, type } from './tokens.ts';

/**
 * Drift guard. `dist/tokens.css` is committed so consumers can `@import` it
 * with no build step — which only stays safe if it is provably the output of
 * `tokens.ts`. This test fails the moment someone hand-edits the CSS or edits
 * the TS without regenerating.
 */
describe('design tokens', () => {
  it('dist/tokens.css matches the TypeScript source', () => {
    const onDisk = readFileSync(OUT_PATH, 'utf8');
    expect(
      onDisk,
      'dist/tokens.css is stale — run `bun run --cwd packages/design-tokens build`',
    ).toBe(render());
  });

  it('light and dark declare exactly the same keys', () => {
    // A key present in one theme and missing from the other resolves to the
    // other theme's value at runtime, which is the classic half-themed bug.
    expect(Object.keys(themes.light).sort()).toEqual(Object.keys(themes.dark).sort());
  });

  it('every emitted custom property is namespaced --sn-', () => {
    // The app imports this into a 6900-line stylesheet with a load-bearing
    // cascade; an un-namespaced --color-* here would silently outrank the
    // app's own @theme block.
    const declared = [...render().matchAll(/^\s*(--[a-z0-9-]+)\s*:/gim)].map((m) => m[1]);
    expect(declared.length).toBeGreaterThan(50);
    expect(declared.filter((n) => !n.startsWith('--sn-'))).toEqual([]);
  });

  it('the spectrum gradient uses every spectrum stop, in order', () => {
    const css = render();
    const gradient = css.match(/--sn-spectrum-gradient:\s*([^;]+);/)?.[1] ?? '';
    let cursor = -1;
    for (const stop of Object.values(spectrum)) {
      const at = gradient.indexOf(stop);
      expect(at, `${stop} missing from the spectrum gradient`).toBeGreaterThan(-1);
      expect(at, `${stop} is out of order in the spectrum gradient`).toBeGreaterThan(cursor);
      cursor = at;
    }
  });

  it('display steps are light-weight with negative tracking', () => {
    // The editorial signature. If a display step drifts to 400+ or to neutral
    // tracking the whole register collapses into generic-SaaS.
    for (const [name, step] of Object.entries(type)) {
      if (!name.startsWith('display-')) continue;
      expect(step.weight, `${name} should be light (300)`).toBe(300);
      expect(step.tracking, `${name} should track negative`).toMatch(/^-/);
    }
  });
});
