/**
 * Global vitest setup — runs before every test file.
 *
 * Installs browser API stubs that jsdom does not implement so that Radix UI
 * primitives (Tooltip, Popover, Dialog, …) and other ResizeObserver consumers
 * can render without crashing.
 *
 * Individual test files that need richer ResizeObserver behaviour (e.g. an
 * immediate-callback stub for canvas sizing) may override `global.ResizeObserver`
 * at module scope; the setup file re-runs before each file, so overrides do not
 * leak between test suites.
 */

// ---------------------------------------------------------------------------
// ResizeObserver — no-op stub for jsdom
// ---------------------------------------------------------------------------

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

if (typeof global.ResizeObserver === "undefined") {
  global.ResizeObserver = ResizeObserverStub as unknown as typeof ResizeObserver;
}
