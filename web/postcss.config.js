// Tailwind v4 setup: the `tailwindcss` package itself is no longer a
// PostCSS plugin (that changed from v3) — the plugin now lives in the
// separate `@tailwindcss/postcss` package, wired in here. See
// src/index.css's `@import "tailwindcss";` for the other half of the setup.
export default {
  plugins: {
    '@tailwindcss/postcss': {},
    autoprefixer: {},
  },
}
