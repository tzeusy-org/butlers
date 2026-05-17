/**
 * ReembedPanel tests — covers:
 *   - Renders pending counts per tier
 *   - Dry-run button calls POST with dry_run: true
 *   - Run button opens confirmation modal
 *   - Confirming modal calls POST with dry_run: false
 *   - Shows loading state (buttons disabled, spinner text) during mutation
 */

// @vitest-environment jsdom

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, cleanup, fireEvent, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import ReembedPanel from "@/components/memory/ReembedPanel";
import { useReembedPending, useReembedRun } from "@/hooks/use-memory-reembed";
import type { ReembedPendingCounts } from "@/api/types";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

vi.mock("@/hooks/use-memory-reembed", () => ({
  useReembedPending: vi.fn(),
  useReembedRun: vi.fn(),
}));

// Radix UI Dialog relies on portals; stub it so tests can query the content
// directly without cross-portal boundary issues in jsdom.
vi.mock("@/components/ui/dialog", () => ({
  Dialog: ({ open, children }: { open: boolean; children: React.ReactNode }) =>
    open ? <div data-testid="dialog">{children}</div> : null,
  DialogContent: ({ children }: { children: React.ReactNode }) => (
    <div data-slot="dialog-content">{children}</div>
  ),
  DialogHeader: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DialogTitle: ({ children }: { children: React.ReactNode }) => <h2>{children}</h2>,
  DialogDescription: ({ children }: { children: React.ReactNode }) => <p>{children}</p>,
  DialogFooter: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

// ---------------------------------------------------------------------------
// Type helpers
// ---------------------------------------------------------------------------

type UseReembedPendingResult = ReturnType<typeof useReembedPending>;
type UseReembedRunResult = ReturnType<typeof useReembedRun>;

const SAMPLE_PENDING: ReembedPendingCounts = {
  counts: { episodes: 12, facts: 5, rules: 3 },
  total: 20,
  current_model: "all-MiniLM-L6-v2",
};


// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
}

function renderPanel(butler?: string) {
  const qc = makeQueryClient();
  return render(
    <QueryClientProvider client={qc}>
      <ReembedPanel butler={butler} />
    </QueryClientProvider>,
  );
}

function setHooks({
  pending = SAMPLE_PENDING,
  mutate = vi.fn(),
  isPending = false,
  isPendingQuery = false,
  variables,
}: {
  pending?: ReembedPendingCounts | null;
  mutate?: ReturnType<typeof vi.fn>;
  isPending?: boolean;
  isPendingQuery?: boolean;
  variables?: { dry_run?: boolean };
}) {
  vi.mocked(useReembedPending).mockReturnValue({
    data: pending ? { data: pending, meta: {} } : undefined,
    isLoading: isPendingQuery,
  } as unknown as UseReembedPendingResult);

  vi.mocked(useReembedRun).mockReturnValue({
    mutate,
    isPending,
    isError: false,
    variables,
  } as unknown as UseReembedRunResult);
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("ReembedPanel", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    setHooks({});
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  // -------------------------------------------------------------------------
  // Renders counts
  // -------------------------------------------------------------------------

  it("renders the panel heading", () => {
    renderPanel();
    expect(screen.getByText("Embedding migration")).toBeDefined();
  });

  it("renders pending counts per tier", () => {
    renderPanel();
    // Tier labels
    expect(screen.getByText("Episodes (Eden)")).toBeDefined();
    expect(screen.getByText("Facts (Mid-term)")).toBeDefined();
    expect(screen.getByText("Rules (Long-term)")).toBeDefined();
    // Row counts (displayed as localised numbers)
    expect(screen.getByText("12")).toBeDefined();
    expect(screen.getByText("5")).toBeDefined();
    expect(screen.getByText("3")).toBeDefined();
    // Total
    expect(screen.getByText("20")).toBeDefined();
  });

  it("renders the current model name", () => {
    renderPanel();
    expect(screen.getByText("all-MiniLM-L6-v2")).toBeDefined();
  });

  it("renders a loading message when counts are being fetched", () => {
    setHooks({ pending: null, isPendingQuery: true });
    renderPanel();
    expect(screen.getByText(/Loading pending counts/)).toBeDefined();
  });

  // -------------------------------------------------------------------------
  // Dry-run
  // -------------------------------------------------------------------------

  it("calls mutate with dry_run: true when Dry-run button is clicked", () => {
    const mutate = vi.fn();
    setHooks({ mutate });
    renderPanel("general");

    const dryRunBtn = screen.getByRole("button", { name: /dry-run/i });
    fireEvent.click(dryRunBtn);

    expect(mutate).toHaveBeenCalledOnce();
    const [body] = mutate.mock.calls[0] as [{ dry_run: boolean }[], ...unknown[]];
    expect(body[0]?.dry_run ?? (body as unknown as { dry_run: boolean }).dry_run).toBe(true);
  });

  // -------------------------------------------------------------------------
  // Run with confirmation
  // -------------------------------------------------------------------------

  it("opens the confirmation modal when Run re-embed is clicked", () => {
    setHooks({});
    renderPanel("general");

    const runBtn = screen.getByRole("button", { name: /run re-embed/i });
    expect(screen.queryByTestId("dialog")).toBeNull();

    fireEvent.click(runBtn);
    expect(screen.getByTestId("dialog")).toBeDefined();
  });

  it("does not call mutate if the modal is cancelled", () => {
    const mutate = vi.fn();
    setHooks({ mutate });
    renderPanel("general");

    fireEvent.click(screen.getByRole("button", { name: /run re-embed/i }));
    // Cancel button inside the modal
    const cancelBtn = screen.getByRole("button", { name: /cancel/i });
    fireEvent.click(cancelBtn);

    expect(mutate).not.toHaveBeenCalled();
    // Modal dismissed
    expect(screen.queryByTestId("dialog")).toBeNull();
  });

  it("calls mutate with dry_run: false when confirmed", () => {
    const mutate = vi.fn();
    setHooks({ mutate });
    renderPanel("general");

    fireEvent.click(screen.getByRole("button", { name: /run re-embed/i }));
    const confirmBtn = screen.getByRole("button", { name: /confirm re-embed/i });
    fireEvent.click(confirmBtn);

    expect(mutate).toHaveBeenCalledOnce();
    // First arg is the request body (first positional argument to mutate)
    const callArgs = mutate.mock.calls[0] as unknown[];
    const body = callArgs[0] as { dry_run: boolean };
    expect(body.dry_run).toBe(false);
  });

  // -------------------------------------------------------------------------
  // Loading state
  // -------------------------------------------------------------------------

  it("disables both buttons when no butler is provided", () => {
    setHooks({});
    renderPanel(); // no butler

    const buttons = screen.getAllByRole("button");
    const actionButtons = buttons.filter(
      (b) => b.textContent?.match(/dry-run|re-embed/i),
    );
    expect(actionButtons.length).toBeGreaterThan(0);
    actionButtons.forEach((btn) => {
      expect((btn as HTMLButtonElement).disabled).toBe(true);
    });
  });

  it("disables both buttons during mutation", () => {
    setHooks({ isPending: true });
    renderPanel("general");

    const buttons = screen.getAllByRole("button");
    const actionButtons = buttons.filter(
      (b) => b.textContent?.match(/dry-run|re-embed/i),
    );
    actionButtons.forEach((btn) => {
      expect((btn as HTMLButtonElement).disabled).toBe(true);
    });
  });

  it("shows 'Running dry-run…' text while dry-run mutation is in flight", () => {
    setHooks({ isPending: true, variables: { dry_run: true } });
    renderPanel("general");
    expect(screen.getByText(/Running dry-run/)).toBeDefined();
  });

  it("shows 'Re-embedding…' text while live mutation is in flight", () => {
    setHooks({ isPending: true, variables: { dry_run: false } });
    renderPanel("general");
    expect(screen.getByText(/Re-embedding/)).toBeDefined();
  });
});
