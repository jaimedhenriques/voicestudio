// @ts-check
import { defineConfig } from 'astro/config';
import tailwindcss from '@tailwindcss/vite';

// Static output: the site is content, not an app. Nothing here needs a server
// at request time, so it deploys to any static host and cannot fall over
// independently of the product.
export default defineConfig({
  site: 'https://sonari.com',
  output: 'static',
  vite: {
    plugins: [tailwindcss()],
  },
});
