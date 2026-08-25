/**
 * Rewrite the `[data-theme="sonari"]` block in `frontend/src/index.css` from
 * `packages/design-tokens/src/app-theme.ts`.
 *
 * The block is stored as colour literals so it matches every other theme in
 * that file and so `themeCascade.test.js` can keep parsing top-level blocks by
 * source order. This script is what keeps those literals honest;
 * `frontend/src/test/sonariTheme.test.js` fails if they drift.
 *
 * Run: bun run sync:app-theme
 */

import { readFileSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { renderAppThemeBlock } from '../packages/design-tokens/src/app-theme.ts';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const CSS_PATH = resolve(ROOT, 'frontend/src/index.css');
const SELECTOR = '[data-theme="sonari"] {';

const css = readFileSync(CSS_PATH, 'utf8');
const start = css.indexOf(SELECTOR);
if (start === -1) {
  console.error(
    `No ${SELECTOR} block in ${CSS_PATH}.\n` +
      'It must sit AFTER every default :root and after the other [data-theme] ' +
      'blocks — see the cascade note at the top of index.css.',
  );
  process.exit(1);
}

// Brace-match rather than searching for "\n}", so a nested block inside the
// theme (there is none today, but rules move) cannot truncate the replacement.
let depth = 0;
let end = -1;
for (let i = css.indexOf('{', start); i < css.length; i++) {
  if (css[i] === '{') depth++;
  else if (css[i] === '}' && --depth === 0) {
    end = i + 1;
    break;
  }
}
if (end === -1) {
  console.error(`Unbalanced braces after ${SELECTOR} in ${CSS_PATH}.`);
  process.exit(1);
}

const next = css.slice(0, start) + renderAppThemeBlock() + css.slice(end);
if (next === css) {
  console.log('index.css already in sync with packages/design-tokens.');
} else {
  writeFileSync(CSS_PATH, next);
  console.log(`Rewrote ${SELECTOR} in ${CSS_PATH}.`);
}
