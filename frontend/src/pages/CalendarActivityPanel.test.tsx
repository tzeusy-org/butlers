// @vitest-environment jsdom
/**
 * CalendarActivityPanel — read-only audit view of workspace mutations.
 *
 * The Activity tab lists logged calendar mutations. The dashboard no longer
 * offers a per-row Undo (bu-q11586.7, owner decision bu-kf3n4i = A); these
 * tests pin that, the degraded-source note, and the never-blank paging.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";

import type { CalendarAuditEntry } from "@/api/types.ts";
import { CalendarActivityPanel } from "@/pages/calendar/CalendarActivityPanel";

function auditEntry(overrides: Partial<CalendarAuditEntry>): CalendarAuditEntry {
  return {
    id: "11111111-1111-1111-1111-111111111111",
    idempotency_key: "idem-1",
    request_id: "req-1",
    action_type: "workspace_user_update",
    action_status: "applied",
    origin_ref: "evt-1",
    payload_summary: { title: "Standup" },
    error: null,
    created_at: "2026-07-10T02:00:00Z",
    updated_at: "2026-07-10T02:00:00Z",
    applied_at: "2026-07-10T02:00:00Z",
    source_butler: "general",
    source_session_id: null,
    ...overrides,
  };
}

function renderPanel(
  entries: CalendarAuditEntry[],
  sourcesAvailable?: boolean,
  queryState: {
    isFetching?: boolean;
    isLoading?: boolean;
    isError?: boolean;
    error?: Error | null;
  } = {},
) {
  const auditQuery = {
    isLoading: false,
    isError: false,
    error: null,
    data: {
      data: {
        entries,
        total: entries.length,
        offset: 0,
        limit: 50,
        ...(sourcesAvailable === undefined
          ? {}
          : { sources_available: sourcesAvailable }),
      },
    },
    ...queryState,
  };
  return render(
    <MemoryRouter>
      <CalendarActivityPanel
        auditQuery={auditQuery}
        offset={0}
        limit={50}
        onPageChange={() => {}}
      />
    </MemoryRouter>,
  );
}

afterEach(() => {
  vi.clearAllMocks();
  cleanup();
});

describe("CalendarActivityPanel — read-only rows", () => {
  it("lists an applied, previously undoable row without an Undo button", () => {
    renderPanel([auditEntry({})]);
    expect(screen.getByText(/workspace_user_update: Standup/)).toBeTruthy();
    expect(screen.queryByTestId("calendar-audit-undo")).toBeNull();
    expect(screen.queryByText("Undo")).toBeNull();
  });
});

describe("CalendarActivityPanel — degraded-source note (bu-yjfk2)", () => {
  it("renders the degraded note when sources_available is false (with rows)", () => {
    renderPanel([auditEntry({})], false);
    expect(screen.getByTestId("audit-sources-degraded")).toBeTruthy();
  });

  it("renders the degraded note when sources_available is false and the log is empty", () => {
    renderPanel([], false);
    // The empty-state must NOT read as a clean, complete history when a source failed.
    expect(screen.getByTestId("audit-sources-degraded")).toBeTruthy();
    expect(screen.getByText(/No calendar mutations logged yet/i)).toBeTruthy();
  });

  it("omits the degraded note when sources_available is true", () => {
    renderPanel([auditEntry({})], true);
    expect(screen.queryByTestId("audit-sources-degraded")).toBeNull();
  });

  it("omits the degraded note when sources_available is absent (default healthy)", () => {
    renderPanel([auditEntry({})]);
    expect(screen.queryByTestId("audit-sources-degraded")).toBeNull();
  });
});

describe("CalendarActivityPanel — never-blank audit pages (bu-plib7)", () => {
  it("keeps previous audit rows visible but dimmed while the next page fetches", () => {
    const { container } = renderPanel([auditEntry({})], undefined, {
      isFetching: true,
    });

    expect(screen.getByText(/workspace_user_update/i)).toBeTruthy();
    expect(container.innerHTML).toContain("opacity-60");
  });

  it("prioritizes a query error over retained audit rows", () => {
    renderPanel([auditEntry({})], undefined, {
      isError: true,
      error: new Error("audit fetch failed"),
    });

    expect(screen.getByRole("alert").textContent).toContain("audit fetch failed");
    expect(screen.queryByText(/workspace_user_update/i)).toBeNull();
  });
});
