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

// ---------------------------------------------------------------------------
// matchMedia — no-op stub for jsdom (required by usePrefersReducedMotion)
//
// jsdom does not implement window.matchMedia; components that read media
// queries (e.g. the `prefers-reduced-motion` cross-fade gate) would otherwise
// throw "window.matchMedia is not a function". The stub reports "no match"
// (motion enabled) and exposes the listener API the hook subscribes to.
// ---------------------------------------------------------------------------

if (typeof window !== "undefined" && typeof window.matchMedia !== "function") {
  window.matchMedia = ((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
}
