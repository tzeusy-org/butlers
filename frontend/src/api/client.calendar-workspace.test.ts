/**
 * Tests for getCalendarWorkspace API client param serialization (bu-xr1i95).
 *
 * Verifies the server-side facet + keyset pagination params are forwarded:
 * - `status`, `source_type`, `editable` facets
 * - `limit` and opaque `cursor`
 * - none of them appear when omitted (back-compat)
 */

import { afterEach, describe, expect, it, vi } from "vitest";

const mockFetch = vi.fn();
global.fetch = mockFetch as unknown as typeof fetch;

afterEach(() => {
  vi.clearAllMocks();
});

function mockWorkspaceResponse() {
  const body = {
    data: { entries: [], source_freshness: [], lanes: [], next_cursor: null, has_more: false },
  };
  mockFetch.mockResolvedValueOnce({
    ok: true,
    status: 200,
    json: async () => body,
    text: async () => JSON.stringify(body),
    headers: { get: () => "application/json" },
  });
}

import { getCalendarWorkspace } from "./client.ts";

const BASE = {
  view: "user" as const,
  start: "2026-02-22T00:00:00Z",
  end: "2026-02-23T00:00:00Z",
};

describe("getCalendarWorkspace — facets + pagination params", () => {
  it("forwards status, source_type, editable, limit, and cursor", async () => {
    mockWorkspaceResponse();
    await getCalendarWorkspace({
      ...BASE,
      status: "paused",
      source_type: "scheduled_task",
      editable: true,
      limit: 50,
      cursor: "opaque-token",
    });
    const url: string = mockFetch.mock.calls[0][0];
    expect(url).toContain("status=paused");
    expect(url).toContain("source_type=scheduled_task");
    expect(url).toContain("editable=true");
    expect(url).toContain("limit=50");
    expect(url).toContain("cursor=opaque-token");
  });

  it("omits the facet/pagination params when not provided (back-compat)", async () => {
    mockWorkspaceResponse();
    await getCalendarWorkspace({ ...BASE });
    const url: string = mockFetch.mock.calls[0][0];
    expect(url).not.toContain("status=");
    expect(url).not.toContain("source_type=");
    expect(url).not.toContain("editable=");
    expect(url).not.toContain("limit=");
    expect(url).not.toContain("cursor=");
  });

  it("serializes editable=false explicitly", async () => {
    mockWorkspaceResponse();
    await getCalendarWorkspace({ ...BASE, editable: false });
    const url: string = mockFetch.mock.calls[0][0];
    expect(url).toContain("editable=false");
  });
});

function mockFindTimeResponse(slots: unknown[]) {
  const body = { data: { slots, duration_minutes: 60, calendar_ids: ["primary"] } };
  mockFetch.mockResolvedValueOnce({
    ok: true,
    status: 200,
    json: async () => body,
    text: async () => JSON.stringify(body),
    headers: { get: () => "application/json" },
  });
}

import { findCalendarWorkspaceTime } from "./client.ts";

describe("findCalendarWorkspaceTime — POST /calendar/workspace/find-time", () => {
  it("POSTs the duration, window, and constraints", async () => {
    const slot = {
      start_at: "2026-06-22T09:00:00+00:00",
      end_at: "2026-06-22T10:00:00+00:00",
      timezone: "UTC",
    };
    mockFindTimeResponse([slot]);

    const res = await findCalendarWorkspaceTime({
      butler_name: "general",
      duration_minutes: 60,
      search_start: "2026-06-22T08:00:00Z",
      search_end: "2026-06-29T08:00:00Z",
      constraints: { part_of_day: "morning", avoid_weekdays: ["SA", "SU"] },
      limit: 12,
    });

    const [url, init] = mockFetch.mock.calls[0];
    expect(url).toContain("/calendar/workspace/find-time");
    expect(init.method).toBe("POST");
    const sent = JSON.parse(init.body as string);
    expect(sent.duration_minutes).toBe(60);
    expect(sent.constraints).toEqual({ part_of_day: "morning", avoid_weekdays: ["SA", "SU"] });
    expect(res.data.slots).toHaveLength(1);
    expect(res.data.slots[0].start_at).toBe(slot.start_at);
  });
});

import { acceptCalendarProposal, ApiError, dismissCalendarProposal } from "./client.ts";

function mockProposalActionResponse(status: string, acceptedEventId?: string | null) {
  const body = {
    data: {
      proposal_id: "11111111-1111-1111-1111-111111111111",
      status,
      accepted_event_id: acceptedEventId ?? null,
      butler_name: "general",
    },
  };
  mockFetch.mockResolvedValueOnce({
    ok: true,
    status: 200,
    json: async () => body,
    text: async () => JSON.stringify(body),
    headers: { get: () => "application/json" },
  });
}

function mockErrorResponse(status: number, detail: string) {
  const body = { detail };
  mockFetch.mockResolvedValueOnce({
    ok: false,
    status,
    statusText: "Error",
    json: async () => body,
    text: async () => JSON.stringify(body),
    headers: { get: () => "application/json" },
  });
}

const PROPOSAL_ID = "11111111-1111-1111-1111-111111111111";

describe("acceptCalendarProposal — POST /calendar/workspace/proposals/{id}/accept", () => {
  it("POSTs to the accept endpoint with an empty body when no overrides given", async () => {
    mockProposalActionResponse("accepted", "22222222-2222-2222-2222-222222222222");

    const res = await acceptCalendarProposal(PROPOSAL_ID);

    const [url, init] = mockFetch.mock.calls[0];
    expect(url).toContain(`/calendar/workspace/proposals/${PROPOSAL_ID}/accept`);
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({});
    expect(res.data.status).toBe("accepted");
    expect(res.data.accepted_event_id).toBe("22222222-2222-2222-2222-222222222222");
  });

  it("forwards inline overrides in the request body", async () => {
    mockProposalActionResponse("accepted", "22222222-2222-2222-2222-222222222222");

    await acceptCalendarProposal(PROPOSAL_ID, {
      title: "Edited title",
      start_at: "2026-06-22T09:00:00Z",
      end_at: "2026-06-22T10:00:00Z",
    });

    const [, init] = mockFetch.mock.calls[0];
    const sent = JSON.parse(init.body as string);
    expect(sent.title).toBe("Edited title");
    expect(sent.start_at).toBe("2026-06-22T09:00:00Z");
    expect(sent.end_at).toBe("2026-06-22T10:00:00Z");
  });

  it("throws an ApiError carrying status 409 when the proposal is already dismissed", async () => {
    mockErrorResponse(409, "Proposal is dismissed and cannot be accepted");

    await expect(acceptCalendarProposal(PROPOSAL_ID)).rejects.toMatchObject({
      status: 409,
    });
  });
});

describe("dismissCalendarProposal — POST /calendar/workspace/proposals/{id}/dismiss", () => {
  it("POSTs to the dismiss endpoint (no body required)", async () => {
    mockProposalActionResponse("dismissed");

    const res = await dismissCalendarProposal(PROPOSAL_ID);

    const [url, init] = mockFetch.mock.calls[0];
    expect(url).toContain(`/calendar/workspace/proposals/${PROPOSAL_ID}/dismiss`);
    expect(init.method).toBe("POST");
    expect(res.data.status).toBe("dismissed");
  });

  it("throws an ApiError carrying status 404 for an unknown proposal", async () => {
    mockErrorResponse(404, "Proposal not found");

    const err = await dismissCalendarProposal(PROPOSAL_ID).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(404);
  });
});

import { undoCalendarWorkspaceMutation } from "./client.ts";

const ACTION_ID = "44444444-4444-4444-4444-444444444444";

describe("undoCalendarWorkspaceMutation — POST /calendar/workspace/undo/{action_id}", () => {
  it("POSTs to the undo endpoint threading the action_id and returns the fresh request_id", async () => {
    const body = {
      data: {
        action_id: ACTION_ID,
        action_type: "workspace_user_update",
        inverse_tool: "calendar_update_event",
        request_id: "undo-deadbeef",
        undone: true,
        result: { event_id: "evt-1" },
      },
    };
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => body,
      text: async () => JSON.stringify(body),
      headers: { get: () => "application/json" },
    });

    const res = await undoCalendarWorkspaceMutation(ACTION_ID);

    const [url, init] = mockFetch.mock.calls[0];
    expect(url).toContain(`/calendar/workspace/undo/${ACTION_ID}`);
    expect(init.method).toBe("POST");
    // No client-supplied body — the endpoint synthesizes the inverse itself.
    expect(init.body).toBeUndefined();
    // The reversal's request_id is generated server-side and surfaced back.
    expect(res.data.request_id).toBe("undo-deadbeef");
    expect(res.data.request_id.startsWith("undo-")).toBe(true);
    expect(res.data.undone).toBe(true);
  });

  it("surfaces 409 (already undone) as an ApiError with the status preserved", async () => {
    mockErrorResponse(409, "Action was already undone.");
    const err = await undoCalendarWorkspaceMutation(ACTION_ID).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(409);
  });

  it("surfaces 422 (missing pre-state) as an ApiError with the status preserved", async () => {
    mockErrorResponse(422, "Captured pre-state is missing or expired.");
    const err = await undoCalendarWorkspaceMutation(ACTION_ID).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(422);
  });

  it("surfaces 404 (unknown action) as an ApiError with the status preserved", async () => {
    mockErrorResponse(404, "Unknown calendar action.");
    const err = await undoCalendarWorkspaceMutation(ACTION_ID).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(404);
  });
});

import { previewCalendarWorkspaceButlerEvent } from "./client.ts";

describe("previewCalendarWorkspaceButlerEvent — POST /calendar/workspace/butler-events/preview", () => {
  it("POSTs the draft recurrence and returns the projection envelope", async () => {
    const body = {
      data: {
        occurrences: ["2026-06-22T09:00:00+00:00", "2026-06-29T09:00:00+00:00"],
        total_in_window: 13,
        more_count: 7,
        window_start: "2026-06-22T09:00:00+00:00",
        window_end: "2026-09-20T09:00:00+00:00",
        effective_cron: "0 9 * * 1",
        notes: ["INTERVAL=2 is not supported by the butler scheduler — ..."],
      },
    };
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => body,
      text: async () => JSON.stringify(body),
      headers: { get: () => "application/json" },
    });

    const res = await previewCalendarWorkspaceButlerEvent({
      rrule: "RRULE:FREQ=WEEKLY;INTERVAL=2",
      start_at: "2026-06-22T09:00:00Z",
      limit: 6,
    });

    const [url, init] = mockFetch.mock.calls[0];
    expect(url).toContain("/calendar/workspace/butler-events/preview");
    expect(init.method).toBe("POST");
    const sent = JSON.parse(init.body as string);
    expect(sent.rrule).toBe("RRULE:FREQ=WEEKLY;INTERVAL=2");
    expect(sent.limit).toBe(6);
    expect(res.data.more_count).toBe(7);
    expect(res.data.notes).toHaveLength(1);
  });
});
