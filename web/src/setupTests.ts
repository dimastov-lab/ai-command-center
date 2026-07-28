// `@testing-library/jest-dom` v7's base entrypoint calls `expect.extend(...)`
// against a Jest-style GLOBAL `expect`, which does not exist here because
// `test.globals` is not enabled in vite.config.ts (it would throw
// "ReferenceError: expect is not defined" at setup time). The `/vitest`
// subpath instead imports `expect` from 'vitest' explicitly and extends
// that, which works regardless of the `globals` setting.
import '@testing-library/jest-dom/vitest'
