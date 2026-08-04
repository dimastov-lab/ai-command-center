// `defineConfig` comes from 'vitest/config' (not 'vite') so the `test` field
// below type-checks under `tsc -b` — vitest's export merges Vite's
// UserConfig with its own `test` options; functionally identical to
// importing from 'vite' plus a `/// <reference types="vitest/config" />`.
import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: { proxy: { '/api': 'http://localhost:8791' } },
  test: { environment: 'jsdom', setupFiles: './src/setupTests.ts' },
})
