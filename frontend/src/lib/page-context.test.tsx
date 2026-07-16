// @vitest-environment jsdom
/**
 * Tests for PageContextProvider / usePageContext / usePageContextCapture
 * (bu-p6ey8.4 — "Page context capture").
 *
 * Covers:
 *  - default capture: route path + query params, no enrichment
 *  - enrichment via usePageContext().set({ entity_ref })
 *  - snapshot-at-send: a page-context change AFTER a capture() call never
 *    mutates the already-returned snapshot
 *  - enrichment clears when the enriching page unmounts (no stale entity_ref
 *    bleeding into a later, unrelated page)
 *  - stale cleanup from an old page cannot clear a newer page's enrichment
 */

import { useEffect, useState } from "react";
import { afterEach, describe, expect, it } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";

import { PageContextProvider, usePageContext, usePageContextCapture } from "./page-context.tsx";

afterEach(() => cleanup());

// ---------------------------------------------------------------------------
// Test harness
// ---------------------------------------------------------------------------

/** Renders a capture button + the JSON of whatever it last captured. */
function CaptureHarness() {
  const capture = usePageContextCapture();
  const [captured, setCaptured] = useState<string>("");
  return (
    <div>
      <button data-testid="capture-btn" onClick={() => setCaptured(JSON.stringify(capture()))}>
        capture
      </button>
      <pre data-testid="captured-json">{captured}</pre>
    </div>
  );
}

/** A page that enriches with `entity_ref` while `entityRef` is non-null. */
function EnrichingPage({ entityRef }: { entityRef: string | null }) {
  const { set } = usePageContext();
  useEffect(() => {
    if (entityRef != null) set({ entity_ref: entityRef });
  }, [entityRef, set]);
  return <div data-testid="enriching-page">{entityRef}</div>;
}

function readCaptured(): Record<string, unknown> {
  return JSON.parse(screen.getByTestId("captured-json").textContent ?? "{}");
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("PageContextProvider / usePageContextCapture — default capture", () => {
  it("captures the current route with no query_params key when there are none", () => {
    render(
      <MemoryRouter initialEntries={["/entities"]}>
        <PageContextProvider>
          <CaptureHarness />
        </PageContextProvider>
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByTestId("capture-btn"));
    expect(readCaptured()).toEqual({ route: "/entities" });
  });

  it("captures route + query params from the current URL", () => {
    render(
      <MemoryRouter initialEntries={["/entities/concentration?predicate=child-of"]}>
        <PageContextProvider>
          <CaptureHarness />
        </PageContextProvider>
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByTestId("capture-btn"));
    expect(readCaptured()).toEqual({
      route: "/entities/concentration",
      query_params: { predicate: "child-of" },
    });
  });
});

describe("PageContextProvider / usePageContext — enrichment", () => {
  it("includes entity_ref set by the mounted page (reference implementation pattern)", () => {
    render(
      <MemoryRouter initialEntries={["/entities/e-123"]}>
        <PageContextProvider>
          <EnrichingPage entityRef="alice" />
          <CaptureHarness />
        </PageContextProvider>
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByTestId("capture-btn"));
    expect(readCaptured()).toEqual({ route: "/entities/e-123", entity_ref: "alice" });
  });

  it("clears enrichment when the enriching page unmounts", () => {
    function Wrapper() {
      const [mounted, setMounted] = useState(true);
      return (
        <div>
          {mounted && <EnrichingPage entityRef="alice" />}
          <CaptureHarness />
          <button data-testid="unmount-btn" onClick={() => setMounted(false)}>
            unmount
          </button>
        </div>
      );
    }

    render(
      <MemoryRouter initialEntries={["/entities/e-123"]}>
        <PageContextProvider>
          <Wrapper />
        </PageContextProvider>
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByTestId("capture-btn"));
    expect(readCaptured().entity_ref).toBe("alice");

    fireEvent.click(screen.getByTestId("unmount-btn"));
    expect(screen.queryByTestId("enriching-page")).toBeNull();

    fireEvent.click(screen.getByTestId("capture-btn"));
    expect(readCaptured()).toEqual({ route: "/entities/e-123" });
  });

  it("keeps a successor page enrichment when the prior page unmounts later", () => {
    function Wrapper() {
      const [pageAMounted, setPageAMounted] = useState(true);
      const [pageBMounted, setPageBMounted] = useState(false);

      return (
        <div>
          {pageAMounted && <EnrichingPage entityRef="page-a" />}
          {pageBMounted && <EnrichingPage entityRef="page-b" />}
          <CaptureHarness />
          <button data-testid="mount-page-b-btn" onClick={() => setPageBMounted(true)}>
            mount page B
          </button>
          <button data-testid="unmount-page-a-btn" onClick={() => setPageAMounted(false)}>
            unmount page A
          </button>
        </div>
      );
    }

    render(
      <MemoryRouter initialEntries={["/entities/e-123"]}>
        <PageContextProvider>
          <Wrapper />
        </PageContextProvider>
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByTestId("capture-btn"));
    expect(readCaptured().entity_ref).toBe("page-a");

    // Page B claims the slot before A's cleanup runs.
    fireEvent.click(screen.getByTestId("mount-page-b-btn"));
    fireEvent.click(screen.getByTestId("capture-btn"));
    expect(readCaptured().entity_ref).toBe("page-b");

    fireEvent.click(screen.getByTestId("unmount-page-a-btn"));
    fireEvent.click(screen.getByTestId("capture-btn"));
    expect(readCaptured()).toEqual({ route: "/entities/e-123", entity_ref: "page-b" });
  });
});

describe("PageContextProvider / usePageContextCapture — snapshot at send time", () => {
  it("a page-context change AFTER capture() never mutates the already-captured snapshot", () => {
    function Wrapper() {
      const [entityRef, setEntityRef] = useState<string | null>(null);
      return (
        <div>
          <EnrichingPage entityRef={entityRef} />
          <CaptureHarness />
          <button data-testid="enrich-btn" onClick={() => setEntityRef("bob")}>
            enrich
          </button>
        </div>
      );
    }

    render(
      <MemoryRouter initialEntries={["/entities/e-123"]}>
        <PageContextProvider>
          <Wrapper />
        </PageContextProvider>
      </MemoryRouter>,
    );

    // Capture BEFORE any enrichment.
    fireEvent.click(screen.getByTestId("capture-btn"));
    const beforeEnrich = readCaptured();
    expect(beforeEnrich).toEqual({ route: "/entities/e-123" });

    // Enrich the page context AFTER that capture.
    act(() => {
      fireEvent.click(screen.getByTestId("enrich-btn"));
    });

    // The already-captured (and displayed) snapshot must be untouched —
    // it's a plain object built at call time, not a live reference.
    expect(readCaptured()).toEqual(beforeEnrich);

    // A fresh capture() call now reflects the enrichment.
    fireEvent.click(screen.getByTestId("capture-btn"));
    expect(readCaptured()).toEqual({ route: "/entities/e-123", entity_ref: "bob" });
  });
});
