// @vitest-environment jsdom
/**
 * Tests for <AttentionLedgerPanel> -- the attention ledger's first reader,
 * surfaced in the Trust Console (bu-tdd4k.4).
 *
 * Covers:
 * - Renders the per-source delivery-vs-suppression table
 * - A suppressed-but-never-delivered source renders the LOUD red banner
 *   (the marquee signal this panel exists to surface)
 * - A healthy source (delivered > 0) does NOT trigger the banner
 * - source_available=false renders the degraded note, not a truthful
 *   "everything is fine" empty state
 * - Empty window (no rows) renders the calm empty-state copy
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, cleanup } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import type { AttentionLedgerSummaryResponse } from "@/api/index.ts";

vi.mock("@/api/index.ts", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/index.ts")>();
  return {
    ...actual,
    getAttentionLedgerSummary: vi.fn(),
  };
});

import { getAttentionLedgerSummary } from "@/api/index.ts";
import { AttentionLedgerPanel } from "@/components/approvals/attention-ledger-panel.tsx";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function makeQueryClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function renderPanel() {
  return render(
    <QueryClientProvider client={makeQueryClient()}>
      <AttentionLedgerPanel />
    </QueryClientProvider>,
  );
}

function summaryResponse(
  overrides: Partial<AttentionLedgerSummaryResponse> = {},
): AttentionLedgerSummaryResponse {
  return {
    since: "2026-07-04T00:00:00Z",
    until: null,
    by_source: [],
    flagged_sources: [],
    source_available: true,
    ...overrides,
  };
}

describe("AttentionLedgerPanel -- suppressed-but-never-delivered flag", () => {
  it("renders the loud banner for a source that is suppressed but never delivered", async () => {
    vi.mocked(getAttentionLedgerSummary).mockResolvedValue(
      summaryResponse({
        by_source: [
          {
            origin_butler: "secrets_lifecycle",
            delivered: 0,
            coalesced: 0,
            deferred: 3,
            suppressed: 120,
            total: 123,
            suppressed_never_delivered: true,
          },
        ],
        flagged_sources: ["secrets_lifecycle"],
      }),
    );

    renderPanel();

    await waitFor(() => {
      expect(screen.getByTestId("attention-ledger-flagged-banner")).toBeTruthy();
    });
    const banner = screen.getByTestId("attention-ledger-flagged-banner");
    expect(banner.textContent).toContain("secrets_lifecycle");
    expect(banner.textContent).toContain("suppressed but has never delivered");

    const row = screen.getByTestId("attention-source-row");
    expect(row.getAttribute("data-flagged")).toBe("true");
  });

  it("does not render the banner for a healthy source", async () => {
    vi.mocked(getAttentionLedgerSummary).mockResolvedValue(
      summaryResponse({
        by_source: [
          {
            origin_butler: "finance",
            delivered: 5,
            coalesced: 1,
            deferred: 0,
            suppressed: 2,
            total: 8,
            suppressed_never_delivered: false,
          },
        ],
        flagged_sources: [],
      }),
    );

    renderPanel();

    await waitFor(() => {
      expect(screen.getByTestId("attention-source-row")).toBeTruthy();
    });
    expect(screen.queryByTestId("attention-ledger-flagged-banner")).toBeNull();
    expect(screen.getByTestId("attention-source-row").getAttribute("data-flagged")).toBe("false");
  });

  it("surfaces multiple flagged sources in one banner", async () => {
    vi.mocked(getAttentionLedgerSummary).mockResolvedValue(
      summaryResponse({
        by_source: [
          {
            origin_butler: "secrets_lifecycle",
            delivered: 0,
            coalesced: 0,
            deferred: 0,
            suppressed: 120,
            total: 120,
            suppressed_never_delivered: true,
          },
          {
            origin_butler: "home",
            delivered: 0,
            coalesced: 0,
            deferred: 0,
            suppressed: 4,
            total: 4,
            suppressed_never_delivered: true,
          },
        ],
        flagged_sources: ["secrets_lifecycle", "home"],
      }),
    );

    renderPanel();

    await waitFor(() => {
      expect(screen.getByTestId("attention-ledger-flagged-banner")).toBeTruthy();
    });
    const banner = screen.getByTestId("attention-ledger-flagged-banner");
    expect(banner.textContent).toContain("2 sources");
    expect(banner.textContent).toContain("secrets_lifecycle");
    expect(banner.textContent).toContain("home");
  });
});

describe("AttentionLedgerPanel -- degraded envelope", () => {
  it("renders the unavailable note when source_available is false, not a calm empty state", async () => {
    vi.mocked(getAttentionLedgerSummary).mockResolvedValue(
      summaryResponse({ source_available: false }),
    );

    renderPanel();

    await waitFor(() => {
      expect(screen.getByTestId("attention-ledger-source-unavailable")).toBeTruthy();
    });
  });

  it("renders the calm empty state when the window truly has no rows", async () => {
    vi.mocked(getAttentionLedgerSummary).mockResolvedValue(summaryResponse());

    renderPanel();

    await waitFor(() => {
      expect(screen.getByText(/No egress activity recorded/i)).toBeTruthy();
    });
    expect(screen.queryByTestId("attention-ledger-source-unavailable")).toBeNull();
    expect(screen.queryByTestId("attention-ledger-flagged-banner")).toBeNull();
  });
});
