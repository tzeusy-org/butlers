// @vitest-environment jsdom
/**
 * Tests for the shared <QueryBoundary> / <SourceDegradedNote> three-way
 * state contract (bu-86c4c.2 -- "truth amnesty").
 *
 * Static-markup tests use renderToStaticMarkup; the onRetry click test uses
 * createRoot + act, following the project pattern in page.test.tsx.
 */
import { act } from "react";
import { afterEach, describe, expect, expectTypeOf, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { keepPreviousData, onlineManager, QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { createRoot, type Root } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";

import { Query, QueryBoundary, SourceDegradedNote } from "@/components/ui/query-boundary";
import { QueryTile } from "@/components/ui/query-tile";

describe("Query -- confirmed data boundary", () => {
  afterEach(() => {
    cleanup();
    onlineManager.setOnline(true);
  });

  function client() {
    return new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  }

  function deferred<T>() {
    let resolve!: (data: T) => void;
    let reject!: (error: Error) => void;
    const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
    return { promise, resolve, reject };
  }

  it("refuses pending and cached-error children, preserves refreshing success, and retries the read", async () => {
    const queryClient = client();
    const first = deferred<{ count: number }>();
    const refresh = deferred<{ count: number }>();
    const read = vi.fn().mockReturnValueOnce(first.promise).mockReturnValueOnce(refresh.promise)
      .mockResolvedValue({ count: 7 });
    const child = vi.fn();
    function View() {
      const q = useQuery<{ count: number }>({ queryKey: ["count"], queryFn: read });
      return <Query q={q} loadingFallback={<p>Loading count</p>}>
        {(data) => {
          expectTypeOf(data).toEqualTypeOf<{ count: number }>();
          child(data);
          return <p>Count {data.count}</p>;
        }}
      </Query>;
    }
    render(<QueryClientProvider client={queryClient}><View /></QueryClientProvider>);
    expect(screen.getByText("Loading count")).toBeTruthy();
    expect(child).not.toHaveBeenCalled();
    await act(async () => first.resolve({ count: 0 }));
    await screen.findByText("Count 0");
    act(() => { void queryClient.refetchQueries({ queryKey: ["count"] }); });
    await screen.findByText("Refreshing");
    expect(screen.getByText("Count 0")).toBeTruthy();
    expect(screen.getByText("Count 0").closest('[aria-busy="true"]')).toBeTruthy();
    child.mockClear();
    await act(async () => refresh.reject(new Error("Read unavailable")));
    await screen.findByRole("alert");
    expect(queryClient.getQueryData(["count"])).toEqual({ count: 0 });
    expect(screen.queryByText("Count 0")).toBeNull();
    expect(child).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await screen.findByText("Count 7");
    expect(read).toHaveBeenCalledTimes(3);
    queryClient.clear();
  });

  it("does not confirm previous-filter placeholders or carry them across an authority key change", async () => {
    const queryClient = client();
    const next = deferred<string[]>();
    const read = vi.fn().mockResolvedValueOnce(["First owner row"]).mockReturnValue(next.promise);
    const child = vi.fn();
    const isEmpty = vi.fn((rows: string[]) => rows.length === 0);
    function View({ authority, filter }: { authority: string; filter: string }) {
      const q = useQuery<string[]>({ queryKey: ["rows", authority, filter], queryFn: read,
        placeholderData: keepPreviousData });
      return <Query q={q} loadingFallback={<p>Waiting for current results</p>}
        empty={{ when: isEmpty, fallback: <p>No rows</p> }}>
        {(data) => { child(data); return <p>{data.join(", ")}</p>; }}
      </Query>;
    }
    const view = render(<QueryClientProvider client={queryClient}><View authority="first" filter="all" /></QueryClientProvider>);
    await screen.findByText("First owner row");
    child.mockClear(); isEmpty.mockClear();
    view.rerender(<QueryClientProvider client={queryClient}><View authority="first" filter="filtered" /></QueryClientProvider>);
    await screen.findByText("Waiting for current results");
    expect(screen.queryByText("First owner row")).toBeNull();
    expect(child).not.toHaveBeenCalled();
    expect(isEmpty).not.toHaveBeenCalled();
    view.rerender(<QueryClientProvider client={queryClient}><View authority="second" filter="all" /></QueryClientProvider>);
    expect(screen.queryByText("First owner row")).toBeNull();
    await act(async () => next.resolve(["Current authority row"]));
    await screen.findByText("Current authority row");
    expect(child).toHaveBeenLastCalledWith(["Current authority row"]);
    queryClient.clear();
  });

  it("shows an offline recovery state without calling data or empty renderers for a paused first read", async () => {
    onlineManager.setOnline(false);
    const queryClient = client();
    const read = vi.fn().mockResolvedValue([]);
    const child = vi.fn();
    const isEmpty = vi.fn();
    function View() {
      const q = useQuery<string[]>({ queryKey: ["offline"], queryFn: read });
      return <Query q={q} loadingFallback={<p>Loading</p>} empty={{ when: isEmpty, fallback: <p>Empty</p> }}>
        {child}
      </Query>;
    }
    render(<QueryClientProvider client={queryClient}><View /></QueryClientProvider>);
    expect(screen.getByRole("status").textContent).toContain("Offline");
    expect(child).not.toHaveBeenCalled();
    expect(isEmpty).not.toHaveBeenCalled();
    expect(read).not.toHaveBeenCalled();
    queryClient.clear();
  });

  it("derives empty only from successful data and leaves partial-source truth to its success presentation", async () => {
    const queryClient = client();
    function View({ sourceAvailable }: { sourceAvailable: boolean }) {
      const q = useQuery({ queryKey: ["partial", sourceAvailable], queryFn: async () => ({ rows: [], sourceAvailable }) });
      return <Query q={q} loadingFallback={<p>Loading</p>}
        empty={{ when: (data) => data.sourceAvailable && data.rows.length === 0, fallback: <p>All clear</p> }}>
        {(data) => <p>{data.sourceAvailable ? data.rows.length : "Source unavailable"}</p>}
      </Query>;
    }
    const view = render(<QueryClientProvider client={queryClient}><View sourceAvailable={false} /></QueryClientProvider>);
    await screen.findByText("Source unavailable");
    expect(screen.queryByText("All clear")).toBeNull();
    view.rerender(<QueryClientProvider client={queryClient}><View sourceAvailable /></QueryClientProvider>);
    await screen.findByText("All clear");
    expect(screen.queryByText("Source unavailable")).toBeNull();
    queryClient.clear();
  });

  it("uses the shared tile error with a native retry and preserves real zero on recovery", async () => {
    const queryClient = client();
    const read = vi.fn().mockRejectedValueOnce(new Error("Down")).mockResolvedValue(0);
    function View() {
      const q = useQuery<number>({ queryKey: ["tile"], queryFn: read });
      return <QueryTile q={q} title="Facts" description="Confirmed facts" errorMessage="Could not load facts."
        loadingFallback={<p>Loading facts</p>} errorTestId="facts-error">
        {(count) => <p>Recorded {count}</p>}
      </QueryTile>;
    }
    render(<QueryClientProvider client={queryClient}><View /></QueryClientProvider>);
    await screen.findByRole("alert");
    expect(screen.getByTestId("facts-error").textContent).toContain("Could not load facts.");
    const keyboard = userEvent.setup();
    await keyboard.tab();
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Retry" }));
    await keyboard.keyboard("{Enter}");
    await screen.findByText("Recorded 0");
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
    expect(read).toHaveBeenCalledTimes(2);
    queryClient.clear();
  });
});

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
