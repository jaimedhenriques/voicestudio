/**
 * Brand constants — one place to change the name.
 *
 * `Sonari` is a working name pending domain and trademark clearance. Every
 * user-visible surface reads it from here, so replacing it is one edit plus a
 * regenerated asset set, not a repo-wide find-and-replace.
 *
 * IMPORTANT — these are *display* names only. The on-disk identifiers stay
 * exactly as they are, per `docs/branding.md`'s compatibility list: the
 * `omnivoice` Python package, `omnivoice-studio` binary/container
 * coordinates, `OMNIVOICE_*` environment variables, `X-OmniVoice-*` headers,
 * and existing data directories. Renaming any of those breaks every installed
 * copy, and no migration for them exists yet.
 */

export const BRAND = {
  /** Product name. One word, capital S. */
  name: 'Sonari',
  /** Legal entity, for footers and licence pages. Placeholder until incorporated. */
  legalName: 'Sonari',
  /** Bare domain, no scheme. */
  domain: 'sonari.com',
  siteUrl: 'https://sonari.com',
  appUrl: 'https://app.sonari.com',
  docsUrl: 'https://sonari.com/docs',

  /** One line, used as the <title> suffix and the OG description lead. */
  tagline: 'Voice models and voice agents you can run yourself.',

  /**
   * The elevator description. Deliberately concrete: what it does, not how it
   * makes you feel. No "elevate", no "seamless", no "next-generation".
   */
  description:
    'Clone a voice from a few seconds of audio, dub video across 646 languages, and ' +
    'build agents that hold a real conversation — on your own machine or on ours.',

  /** Upstream attribution. AGPL-3.0 §5 requires the notice; keeping it is also just correct. */
  upstream: {
    name: 'VoiceStudio',
    url: 'https://github.com/debpalash/VoiceStudio',
    licence: 'AGPL-3.0-only',
  },
} as const;

/**
 * Nav and footer structure lives here too, so the marketing site and any
 * future in-app upsell surface cannot drift apart.
 */
export const NAV = {
  product: [
    { label: 'Agents', href: '/agents' },
    { label: 'Voices', href: '/voices' },
    { label: 'Dubbing', href: '/dubbing' },
    { label: 'Pricing', href: '/pricing' },
  ],
  resources: [
    { label: 'Docs', href: '/docs' },
    { label: 'Changelog', href: '/changelog' },
    { label: 'Download', href: '/download' },
  ],
  legal: [
    { label: 'Privacy', href: '/legal/privacy' },
    { label: 'Terms', href: '/legal/terms' },
    { label: 'Licence', href: '/legal/licence' },
    { label: 'AI disclosure', href: '/legal/ai-disclosure' },
  ],
} as const;

export type BrandNavItem = { label: string; href: string };
