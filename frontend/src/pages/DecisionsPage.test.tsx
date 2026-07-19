// @vitest-environment jsdom
/**
 * Tests for DecisionsPage (bu-ckkpz.2, epic bu-ckkpz "Owner Decision Desk").
 *
 * Verifies:
 * - Verdict opener + row list render from GET /api/decisions data.
 * - Degraded envelope: decisions_available=false never renders the calm
 *   "No decisions waiting." empty state.
 * - Genuine empty digest renders the honest all-clear.
 * - j/k roving selection (useListTriage) expands the selected row's detail
 *   panel -- each row is a door to what the digest knows about it.
 */

import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import DecisionsPage from "@/pages/DecisionsPage";

vi.mock("@/hooks/use-decisions", () => ({ useDecisions: vi.fn() }));

import { useDecisions } from "@/hooks/use-decisions";
import type { DecisionBeadSummary, DecisionsListResponse } from "@/api/index.ts";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyMock = any;

function decision(overrides: Partial<DecisionBeadSummary> = {}): DecisionBeadSummary {
  return {
    id: "bu-v4ipc",
    title: "DECISION REQUIRED (owner): connector identity",
    priority: 1,
    created_at: "2026-07-01T00:00:00Z",
    age_hours: 240,
    escalated: false,
    ...overrides,
  };
}

function mockDecisions(
  rows: DecisionBeadSummary[],
  meta: DecisionsListResponse["meta"] = { decisions_available: true },
  overrides: Partial<AnyMock> = {},
) {
  vi.mocked(useDecisions).mockReturnValue({
    data: { data: rows, meta },
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
    ...overrides,
  } as AnyMock);
}

function renderPage(): string {
  const queryClient = new QueryClient();
  return renderToStaticMarkup(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <DecisionsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("DecisionsPage -- verdict opener + row list", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("renders the verdict opener and row titles from the digest", () => {
    mockDecisions([
      decision({ id: "bu-a", title: "DECISION REQUIRED (owner): pick A", age_hours: 240 }),
      decision({ id: "bu-b", title: "DECISION REQUIRED (owner): pick B", age_hours: 48 }),
    ]);
    const html = renderPage();
    expect(html).toContain("2 decisions waiting, oldest 10d");
    expect(html).toContain("DECISION REQUIRED (owner): pick A");
    expect(html).toContain("DECISION REQUIRED (owner): pick B");
  });

  it("renders the escalated badge + blocking detail on an escalated row", () => {
    mockDecisions([
      decision({
        id: "bu-v4ipc",
        escalated: true,
        escalated_blocked_id: "bu-wzbu9",
        escalated_blocked_title: "Silent message loss",
        escalated_blocked_kind: "p1_bug",
        escalated_block_hours: 72,
      }),
    ]);
    const html = renderPage();
    expect(html).toContain("escalated");
    expect(html).toContain("blocking a P1 bug bu-wzbu9 for 3d");
  });

  it("renders the honest all-clear when the digest is genuinely empty", () => {
    mockDecisions([]);
    const html = renderPage();
    expect(html).toContain("No decisions waiting.");
  });
});

describe("DecisionsPage -- degraded envelope (never a fabricated all-clear)", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("never renders 'No decisions waiting.' when decisions_available is false", () => {
    mockDecisions([], { decisions_available: false, unavailable_reason: "export_missing" });
    const html = renderPage();
    expect(html).not.toContain("No decisions waiting.");
    expect(html).toContain('data-testid="decisions-degraded"');
    expect(html).toContain("export_missing");
  });

  it("never renders the calm all-clear verdict line either", () => {
    mockDecisions([], { decisions_available: false, unavailable_reason: "export_stale" });
    const html = renderPage();
    expect(html).not.toContain('data-testid="decisions-verdict-all-clear"');
    expect(html).toContain("decision digest unavailable");
  });
});

describe("DecisionsPage -- export as-of plaque (bu-hmdqz.6)", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("renders no plaque when export_as_of is absent", () => {
    mockDecisions([], { decisions_available: true });
    const html = renderPage();
    expect(html).not.toContain('data-testid="decisions-export-as-of"');
  });

  it("renders a muted plaque for a recent export", () => {
    mockDecisions([], {
      decisions_available: true,
      export_as_of: new Date(Date.now() - 60 * 60 * 1000).toISOString(), // 1h ago
    });
    const html = renderPage();
    expect(html).toContain('data-testid="decisions-export-as-of"');
    expect(html).toContain("export as of");
    expect(html).not.toContain("--amber-text");
  });

  it("renders a warning-tinted plaque for a stale-but-available export", () => {
    mockDecisions([], {
      decisions_available: true,
      export_as_of: new Date(Date.now() - 72 * 60 * 60 * 1000).toISOString(), // 3d ago
    });
    const html = renderPage();
    expect(html).toContain('data-testid="decisions-export-as-of"');
    expect(html).toContain("amber-text");
    expect(html).toContain("3d ago");
  });

  it("also renders alongside the degraded-unavailable note when export_as_of is known", () => {
    // e.g. export_stale: unavailable, but the mtime is still known and worth showing.
    mockDecisions([], {
      decisions_available: false,
      unavailable_reason: "export_stale",
      export_as_of: new Date(Date.now() - 15 * 24 * 60 * 60 * 1000).toISOString(),
    });
    const html = renderPage();
    expect(html).toContain('data-testid="decisions-degraded"');
    expect(html).toContain('data-testid="decisions-export-as-of"');
  });
});

describe("DecisionsPage -- j/k roving selection expands the door", () => {
  let container: HTMLDivElement | undefined;
  let root: Root | undefined;

  beforeEach(() => {
    vi.resetAllMocks();
    mockDecisions([
      decision({ id: "bu-a", title: "Pick A" }),
      decision({ id: "bu-b", title: "Pick B" }),
    ]);
  });

  afterEach(() => {
    if (root) {
      act(() => {
        root!.unmount();
      });
    }
    container?.remove();
    container = undefined;
    root = undefined;
  });

  function renderLive() {
    const queryClient = new QueryClient();
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    const r = root;
    act(() => {
      r.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <DecisionsPage />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
  }

  function press(key: string) {
    window.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true }));
  }

  it("j selects the first row and reveals its inline detail panel", () => {
    renderLive();
    act(() => press("j"));

    const rows = container!.querySelectorAll('[data-testid="decision-item"]');
    expect(rows.length).toBe(2);
    expect(container!.querySelector('[data-testid="decision-detail"]')).not.toBeNull();
  });

  it("renders the footer hint strip advertising j/k", () => {
    renderLive();
    act(() => press("j"));
    expect(container!.textContent).toContain("Next item");
    expect(container!.textContent).toContain("Previous item");
  });

  it("clicking a row selects it and reveals its detail panel", () => {
    renderLive();
    const secondRow = container!.querySelectorAll('[data-testid="decision-item"]')[1] as HTMLButtonElement;
    act(() => {
      secondRow.click();
    });
    const detailPanels = container!.querySelectorAll('[data-testid="decision-detail"]');
    expect(detailPanels.length).toBe(1);
    expect(secondRow.textContent).toContain("Pick B");
    expect(secondRow.textContent).toContain("No actions are available in this read-only digest.");
    expect(secondRow.textContent).toContain("decision label");
    expect(secondRow.textContent).not.toContain("title marker");
  });
});
