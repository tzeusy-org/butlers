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

// ---------------------------------------------------------------------------
// scrollIntoView — no-op stub for jsdom (required by cmdk 1.1.1)
//
// cmdk calls element.scrollIntoView() when managing keyboard selection state.
// jsdom does not implement this method; without the stub cmdk throws
// "TypeError: i.scrollIntoView is not a function".
// ---------------------------------------------------------------------------

if (typeof window !== "undefined") {
  window.HTMLElement.prototype.scrollIntoView = function () {};
}
