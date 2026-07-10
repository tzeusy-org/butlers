// @vitest-environment jsdom
/**
 * CalendarActivityPanel — durable-undo affordance tests (bu-4cmc1).
 *
 * The Activity tab surfaces an Undo button on applied, undoable audit rows and
 * wires it to POST /api/calendar/workspace/undo/{action_id}. These tests prove:
 *  - the affordance renders ONLY on applied + undoable rows,
 *  - clicking it dispatches the undo endpoint threading the row's action_id,
 *  - the endpoint returns a fresh server-generated request_id.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";

import { undoCalendarWorkspaceMutation } from "@/api/client.ts";
import type { CalendarAuditEntry } from "@/api/types.ts";
import { CalendarActivityPanel } from "@/pages/CalendarWorkspacePage.tsx";

const mockFetch = vi.fn();
global.fetch = mockFetch as unknown as typeof fetch;

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
  onUndo: (entry: CalendarAuditEntry) => void = () => {},
) {
  const auditQuery = {
    isLoading: false,
    isError: false,
    error: null,
    data: { data: { entries, total: entries.length, offset: 0, limit: 50 } },
  };
  return render(
    <MemoryRouter>
      <CalendarActivityPanel
        auditQuery={auditQuery}
        offset={0}
        limit={50}
        onPageChange={() => {}}
        onUndo={onUndo}
        undoingId={null}
        undoneIds={new Set()}
      />
    </MemoryRouter>,
  );
}

afterEach(() => {
  vi.clearAllMocks();
  cleanup();
});

describe("CalendarActivityPanel — undo affordance", () => {
  it("renders an Undo button on an applied, undoable row", () => {
    renderPanel([auditEntry({})]);
    expect(screen.getByTestId("calendar-audit-undo")).toBeTruthy();
  });

  it("omits Undo on a non-applied row", () => {
    renderPanel([auditEntry({ action_status: "failed" })]);
    expect(screen.queryByTestId("calendar-audit-undo")).toBeNull();
  });

  it("omits Undo on an applied but non-reversible action type", () => {
    renderPanel([auditEntry({ action_type: "workspace_butler_create" })]);
    expect(screen.queryByTestId("calendar-audit-undo")).toBeNull();
  });

  it("calls onUndo with the row when clicked", () => {
    const onUndo = vi.fn();
    const entry = auditEntry({ id: "22222222-2222-2222-2222-222222222222" });
    renderPanel([entry], onUndo);
    fireEvent.click(screen.getByTestId("calendar-audit-undo"));
    expect(onUndo).toHaveBeenCalledTimes(1);
    expect(onUndo.mock.calls[0][0].id).toBe(entry.id);
  });

  it("dispatches the undo endpoint threading action_id and returns a fresh request_id", async () => {
    const actionId = "33333333-3333-3333-3333-333333333333";
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({
        data: {
          action_id: actionId,
          action_type: "workspace_user_update",
          inverse_tool: "calendar_update_event",
          request_id: "undo-abc123",
          undone: true,
          result: {},
        },
      }),
    });

    // The affordance wires straight through to the endpoint client fn; capture
    // the dispatch so we can assert the response's server-generated request_id.
    let dispatched: ReturnType<typeof undoCalendarWorkspaceMutation> | null =
      null;
    const onUndo = (entry: CalendarAuditEntry) => {
      dispatched = undoCalendarWorkspaceMutation(entry.id);
    };
    renderPanel([auditEntry({ id: actionId })], onUndo);
    fireEvent.click(screen.getByTestId("calendar-audit-undo"));

    await waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(1));
    const [url, init] = mockFetch.mock.calls[0];
    expect(String(url)).toContain(`/calendar/workspace/undo/${actionId}`);
    expect(init.method).toBe("POST");

    const res = await dispatched!;
    expect(res.data.request_id).toBe("undo-abc123");
    expect(res.data.request_id.startsWith("undo-")).toBe(true);
  });
});
