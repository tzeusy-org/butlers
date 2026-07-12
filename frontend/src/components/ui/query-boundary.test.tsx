// @vitest-environment jsdom
/**
 * Tests for the shared <QueryBoundary> / <SourceDegradedNote> three-way
 * state contract (bu-86c4c.2 -- "truth amnesty").
 *
 * Static-markup tests use renderToStaticMarkup; the onRetry click test uses
 * createRoot + act, following the project pattern in page.test.tsx.
 */
import { act } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createRoot, type Root } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";

import { QueryBoundary, SourceDegradedNote } from "@/components/ui/query-boundary";

describe("QueryBoundary -- state priority", () => {
  it("renders the loading fallback when isLoading, even if isError is also true", () => {
    const html = renderToStaticMarkup(
      <QueryBoundary
        isLoading
        isError
        error={new Error("boom")}
        isEmpty={false}
        loadingFallback={<div data-testid="loading">Loading…</div>}
        emptyFallback={<div>Empty</div>}
      >
        <div>Content</div>
      </QueryBoundary>,
    );
    expect(html).toContain("Loading…");
    expect(html).not.toContain("Content");
  });

  it("renders the error state (never empty) when isError is true and isEmpty is also true", () => {
    // This is the exact defect class the component exists to prevent: a
    // failed fetch must never fall through to the empty branch.
    const html = renderToStaticMarkup(
      <QueryBoundary
        isLoading={false}
        isError
        error={new Error("network down")}
        isEmpty
        loadingFallback={<div>Loading</div>}
        emptyFallback={<div data-testid="empty">No pending approvals.</div>}
      >
        <div>Content</div>
      </QueryBoundary>,
    );
    expect(html).not.toContain("No pending approvals.");
    expect(html).not.toContain("Content");
    expect(html).toContain('role="alert"');
    expect(html).toContain("network down");
  });

  it("renders the empty fallback when isEmpty and not loading/error", () => {
    const html = renderToStaticMarkup(
      <QueryBoundary
        isLoading={false}
        isError={false}
        isEmpty
        loadingFallback={<div>Loading</div>}
        emptyFallback={<div data-testid="empty">Nothing here.</div>}
      >
        <div>Content</div>
      </QueryBoundary>,
    );
    expect(html).toContain("Nothing here.");
    expect(html).not.toContain("Content");
  });

  it("renders children when not loading, not error, and not empty", () => {
    const html = renderToStaticMarkup(
      <QueryBoundary
        isLoading={false}
        isError={false}
        isEmpty={false}
        loadingFallback={<div>Loading</div>}
        emptyFallback={<div>Empty</div>}
      >
        <div data-testid="content">Real content</div>
      </QueryBoundary>,
    );
    expect(html).toContain("Real content");
  });

  it("extracts a message from a plain object error (e.g. a FastAPI detail payload)", () => {
    const html = renderToStaticMarkup(
      <QueryBoundary
        isLoading={false}
        isError
        error={{ detail: "Service unavailable." }}
        isEmpty
        loadingFallback={<div>Loading</div>}
        emptyFallback={<div>Empty</div>}
      >
        <div>Content</div>
      </QueryBoundary>,
    );
    expect(html).toContain("Service unavailable.");
  });

  it("uses sourceLabel and errorMessage to compose the default error copy", () => {
    const html = renderToStaticMarkup(
      <QueryBoundary
        isLoading={false}
        isError
        isEmpty
        sourceLabel="the health record"
        errorMessage="Server returned 503."
        loadingFallback={<div>Loading</div>}
        emptyFallback={<div>Empty</div>}
      >
        <div>Content</div>
      </QueryBoundary>,
    );
    expect(html).toContain("reach the health record");
    expect(html).toContain("Server returned 503.");
  });
});

describe("QueryBoundary -- onRetry click", () => {
  let container: HTMLElement;
  let root: Root;

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
  });

  it("calls onRetry when the Retry button is clicked", async () => {
    const onRetry = vi.fn();
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    await act(async () => {
      root.render(
        <QueryBoundary
          isLoading={false}
          isError
          error={new Error("down")}
          isEmpty
          onRetry={onRetry}
          loadingFallback={<div>Loading</div>}
          emptyFallback={<div>Empty</div>}
        >
          <div>Content</div>
        </QueryBoundary>,
      );
    });
    const retryBtn = container.querySelector("button");
    expect(retryBtn).not.toBeNull();
    expect(retryBtn?.textContent).toBe("Retry");
    act(() => {
      retryBtn!.click();
    });
    expect(onRetry).toHaveBeenCalledOnce();
  });
});

describe("SourceDegradedNote", () => {
  it("renders role=alert with the label and detail, never silently disappearing", () => {
    const html = renderToStaticMarkup(<SourceDegradedNote label="Connectors" detail="unavailable" />);
    expect(html).toContain('role="alert"');
    expect(html).toContain("Connectors");
    expect(html).toContain("unavailable");
  });

  it("renders a Retry action when onRetry is provided", async () => {
    const onRetry = vi.fn();
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    await act(async () => {
      root.render(<SourceDegradedNote label="Spend today" onRetry={onRetry} />);
    });
    const retryBtn = container.querySelector("button");
    expect(retryBtn?.textContent).toBe("Retry");
    act(() => {
      retryBtn!.click();
    });
    expect(onRetry).toHaveBeenCalledOnce();
    act(() => {
      root.unmount();
    });
    container.remove();
  });

  it("overrides the action label via retryLabel (e.g. 'Sync now' for a freshness plaque)", async () => {
    const onRetry = vi.fn();
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    await act(async () => {
      root.render(
        <SourceDegradedNote
          label="Calendar sync"
          detail="Last synced Apr 7 — 96 days ago"
          onRetry={onRetry}
          retryLabel="Sync now"
        />,
      );
    });
    const actionBtn = container.querySelector("button");
    expect(actionBtn?.textContent).toBe("Sync now");
    act(() => {
      actionBtn!.click();
    });
    expect(onRetry).toHaveBeenCalledOnce();
    act(() => {
      root.unmount();
    });
    container.remove();
  });
});
