// @vitest-environment jsdom
// ---------------------------------------------------------------------------
// Tests for WorkScheduleSettings (bu-whhll.11)
//
// Covers:
//   - Degraded fetch → SourceDegradedNote, NOT a calm empty state
//   - Loading skeleton
//   - Empty (no routines) → explicit empty copy + declare form
//   - Declared vs mined rows: origin badge, edit/delete only on declared
//   - Declare form present and wired
// ---------------------------------------------------------------------------

import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";

vi.mock("@/hooks/use-chronicles", () => ({
  useChroniclesRoutines: vi.fn(),
  useCreateChroniclesRoutine: vi.fn(),
  useUpdateChroniclesRoutine: vi.fn(),
  useDeleteChroniclesRoutine: vi.fn(),
}));

import {
  useChroniclesRoutines,
  useCreateChroniclesRoutine,
  useUpdateChroniclesRoutine,
  useDeleteChroniclesRoutine,
} from "@/hooks/use-chronicles";

import { WorkScheduleSettings } from "./WorkScheduleSettings";
import type { ChroniclerRoutine } from "@/api/types";

function makeRoutine(overrides: Partial<ChroniclerRoutine>): ChroniclerRoutine {
  return {
    id: "r1",
    dow_mask: 0b0011111,
    window_start_local: "09:30:00",
    window_end_local: "19:30:00",
    timezone: "Asia/Singapore",
    label: "Work at Acme",
    support_count: 0,
    confidence: 0,
    evidence_summary: {},
    origin: "declared",
    enabled: true,
    last_confirmed_at: null,
    missed_mine_cycles: 0,
    stale: false,
    created_at: "2026-07-01T00:00:00Z",
    updated_at: "2026-07-01T00:00:00Z",
    ...overrides,
  };
}

const idleMutation = {
  mutate: vi.fn(),
  isPending: false,
  isError: false,
};

function mockRoutines(state: {
  data?: { data: ChroniclerRoutine[]; meta: Record<string, unknown> };
  isLoading?: boolean;
  isError?: boolean;
}) {
  vi.mocked(useChroniclesRoutines).mockReturnValue({
    data: state.data,
    isLoading: state.isLoading ?? false,
    isError: state.isError ?? false,
    refetch: vi.fn(),
  } as unknown as ReturnType<typeof useChroniclesRoutines>);
}

function render(): string {
  return renderToStaticMarkup(<WorkScheduleSettings />);
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(useCreateChroniclesRoutine).mockReturnValue(
    idleMutation as unknown as ReturnType<typeof useCreateChroniclesRoutine>,
  );
  vi.mocked(useUpdateChroniclesRoutine).mockReturnValue(
    idleMutation as unknown as ReturnType<typeof useUpdateChroniclesRoutine>,
  );
  vi.mocked(useDeleteChroniclesRoutine).mockReturnValue(
    idleMutation as unknown as ReturnType<typeof useDeleteChroniclesRoutine>,
  );
});

describe("WorkScheduleSettings — degraded fetch", () => {
  it("renders a degraded note and NOT the empty state when the fetch errored", () => {
    mockRoutines({ isError: true });
    const html = render();
    // Degraded note names the source; empty state must not appear.
    expect(html).toContain("Work schedule");
    expect(html).toContain("couldn&#x27;t load your declared routines");
    expect(html).not.toContain("No schedule declared yet");
  });
});

describe("WorkScheduleSettings — loading", () => {
  it("shows a loading skeleton", () => {
    mockRoutines({ isLoading: true, data: undefined });
    const html = render();
    expect(html).toContain("work-schedule-loading");
    expect(html).not.toContain("No schedule declared yet");
  });
});

describe("WorkScheduleSettings — empty", () => {
  it("shows the explicit empty copy plus the declare form", () => {
    mockRoutines({ data: { data: [], meta: {} } });
    const html = render();
    expect(html).toContain("work-schedule-empty");
    expect(html).toContain("No schedule declared yet");
    // Declare form is always available.
    expect(html).toContain("Declare a schedule");
    expect(html).toContain("declare-submit");
  });
});

describe("WorkScheduleSettings — rows", () => {
  it("renders a declared row with edit + delete controls", () => {
    mockRoutines({ data: { data: [makeRoutine({ id: "d1", origin: "declared" })], meta: {} } });
    const html = render();
    expect(html).toContain("routine-row-d1");
    expect(html).toContain(">declared<");
    expect(html).toContain("routine-edit-d1");
    expect(html).toContain("routine-delete-d1");
    expect(html).toContain("routine-toggle-d1");
    // Formatted schedule appears.
    expect(html).toContain("Mon–Fri");
    expect(html).toContain("09:30–19:30");
  });

  it("renders a mined row WITHOUT edit/delete (toggle only)", () => {
    mockRoutines({
      data: { data: [makeRoutine({ id: "m1", origin: "mined", label: "Mined pattern" })], meta: {} },
    });
    const html = render();
    expect(html).toContain("routine-row-m1");
    expect(html).toContain(">mined<");
    expect(html).toContain("routine-toggle-m1");
    // No schedule edit/delete affordances on a mined routine.
    expect(html).not.toContain("routine-edit-m1");
    expect(html).not.toContain("routine-delete-m1");
  });

  it("marks a disabled routine with a disabled badge", () => {
    mockRoutines({ data: { data: [makeRoutine({ id: "d2", enabled: false })], meta: {} } });
    const html = render();
    expect(html).toContain(">disabled<");
  });

  it("shows evidence-backed staleness and the last confirmation for a mined row", () => {
    mockRoutines({
      data: {
        data: [
          makeRoutine({
            id: "m-stale",
            origin: "mined",
            last_confirmed_at: "2026-07-01T00:00:00Z",
            missed_mine_cycles: 2,
            stale: true,
          }),
        ],
        meta: {},
      },
    });

    const html = render();

    expect(html).toContain("2 missed mines");
    expect(html).toContain("Last confirmed:");
    expect(html).toContain("routine-staleness-m-stale");
  });

  it("surfaces a toggle/update failure outside edit mode instead of failing silently", () => {
    mockRoutines({ data: { data: [makeRoutine({ id: "d3", origin: "declared" })], meta: {} } });
    vi.mocked(useUpdateChroniclesRoutine).mockReturnValue({
      ...idleMutation,
      isError: true,
    } as unknown as ReturnType<typeof useUpdateChroniclesRoutine>);
    const html = render();
    expect(html).toContain("routine-update-error-d3");
    expect(html).toContain("Couldn&#x27;t update this schedule");
  });
});
