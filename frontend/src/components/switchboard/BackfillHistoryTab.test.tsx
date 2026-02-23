// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { BackfillHistoryTab } from "@/components/switchboard/BackfillHistoryTab";
import * as useBackfill from "@/hooks/use-backfill";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

// ---------------------------------------------------------------------------
// Minimal mock shapes
// ---------------------------------------------------------------------------

type QueryResult<T> = {
  data: T | undefined;
  isLoading: boolean;
  error: Error | null;
};

type MutationResult = {
  mutate: ReturnType<typeof vi.fn>;
  mutateAsync: ReturnType<typeof vi.fn>;
  isPending: boolean;
};

function makeQuery<T>(data: T | undefined, isLoading = false): QueryResult<T> {
  return { data, isLoading, error: null };
}

function makeMutation(): MutationResult {
  return { mutate: vi.fn(), mutateAsync: vi.fn().mockResolvedValue({}), isPending: false };
}

const SAMPLE_JOB = {
  id: "job-abc-123",
  connector_type: "gmail",
  endpoint_identity: "user@example.com",
  target_categories: ["email"],
  date_from: "2026-01-01",
  date_to: "2026-01-31",
  rate_limit_per_hour: 100,
  daily_cost_cap_cents: 500,
  status: "pending" as const,
  rows_processed: 0,
  rows_skipped: 0,
  cost_spent_cents: 0,
  error: null,
  created_at: "2026-02-23T10:00:00Z",
  started_at: null,
  completed_at: null,
  updated_at: "2026-02-23T10:00:00Z",
};

const SAMPLE_CONNECTOR = {
  connector_type: "gmail",
  endpoint_identity: "user@example.com",
  instance_id: null,
  version: null,
  state: "healthy",
  error_message: null,
  uptime_s: null,
  last_heartbeat_at: null,
  first_seen_at: "2026-01-01T00:00:00Z",
  registered_via: "self",
  counter_messages_ingested: 0,
  counter_messages_failed: 0,
  counter_source_api_calls: 0,
  counter_checkpoint_saves: 0,
  counter_dedupe_accepted: 0,
  checkpoint_cursor: null,
  checkpoint_updated_at: null,
};

// ---------------------------------------------------------------------------
// Render helper
// ---------------------------------------------------------------------------

let container: HTMLDivElement;
let root: Root;

function render() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  act(() => {
    root.render(
      <QueryClientProvider client={qc}>
        <BackfillHistoryTab />
      </QueryClientProvider>,
    );
  });
}

// ---------------------------------------------------------------------------
// Setup / teardown
// ---------------------------------------------------------------------------

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);

  // Default: no jobs, no connectors, no mutations pending
  vi.spyOn(useBackfill, "useBackfillJobs").mockReturnValue(
    makeQuery({ data: [], meta: { total: 0, offset: 0, limit: 20 } }) as ReturnType<typeof useBackfill.useBackfillJobs>,
  );
  vi.spyOn(useBackfill, "useBackfillJobProgress").mockReturnValue(
    makeQuery(undefined) as ReturnType<typeof useBackfill.useBackfillJobProgress>,
  );
  vi.spyOn(useBackfill, "useConnectors").mockReturnValue(
    makeQuery({ data: [], meta: {} }) as ReturnType<typeof useBackfill.useConnectors>,
  );
  vi.spyOn(useBackfill, "useCreateBackfillJob").mockReturnValue(
    makeMutation() as unknown as ReturnType<typeof useBackfill.useCreateBackfillJob>,
  );
  vi.spyOn(useBackfill, "usePauseBackfillJob").mockReturnValue(
    makeMutation() as unknown as ReturnType<typeof useBackfill.usePauseBackfillJob>,
  );
  vi.spyOn(useBackfill, "useCancelBackfillJob").mockReturnValue(
    makeMutation() as unknown as ReturnType<typeof useBackfill.useCancelBackfillJob>,
  );
  vi.spyOn(useBackfill, "useResumeBackfillJob").mockReturnValue(
    makeMutation() as unknown as ReturnType<typeof useBackfill.useResumeBackfillJob>,
  );
});

afterEach(() => {
  act(() => { root.unmount(); });
  container.remove();
  vi.restoreAllMocks();
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("BackfillHistoryTab", () => {
  it("renders heading and create button", () => {
    render();
    expect(container.querySelector("h2")?.textContent).toContain("Backfill History");
    const btn = container.querySelector('[data-testid="create-backfill-btn"]');
    expect(btn).not.toBeNull();
  });

  it("shows empty state when no jobs", () => {
    render();
    expect(container.textContent).toContain("No backfill jobs found");
  });

  it("shows skeleton rows while loading", () => {
    vi.spyOn(useBackfill, "useBackfillJobs").mockReturnValue(
      { data: undefined, isLoading: true, error: null } as unknown as ReturnType<typeof useBackfill.useBackfillJobs>,
    );
    render();
    // Skeletons render empty cells — just ensure no crash and the table exists
    const table = container.querySelector("table");
    expect(table).not.toBeNull();
  });

  it("renders job rows when jobs are present", () => {
    vi.spyOn(useBackfill, "useBackfillJobs").mockReturnValue(
      makeQuery({
        data: [SAMPLE_JOB],
        meta: { total: 1, offset: 0, limit: 20 },
      }) as ReturnType<typeof useBackfill.useBackfillJobs>,
    );
    render();
    const row = container.querySelector(`[data-testid="job-row-${SAMPLE_JOB.id}"]`);
    expect(row).not.toBeNull();
    expect(container.textContent).toContain("gmail");
    expect(container.textContent).toContain("user@example.com");
  });

  it("shows pause and cancel buttons for pending job with online connector", () => {
    vi.spyOn(useBackfill, "useBackfillJobs").mockReturnValue(
      makeQuery({
        data: [SAMPLE_JOB],
        meta: { total: 1, offset: 0, limit: 20 },
      }) as ReturnType<typeof useBackfill.useBackfillJobs>,
    );
    vi.spyOn(useBackfill, "useConnectors").mockReturnValue(
      makeQuery({ data: [SAMPLE_CONNECTOR], meta: {} }) as ReturnType<typeof useBackfill.useConnectors>,
    );
    render();
    expect(container.querySelector(`[data-testid="pause-btn-${SAMPLE_JOB.id}"]`)).not.toBeNull();
    expect(container.querySelector(`[data-testid="cancel-btn-${SAMPLE_JOB.id}"]`)).not.toBeNull();
  });

  it("shows resume button for paused job with online connector", () => {
    const pausedJob = { ...SAMPLE_JOB, status: "paused" as const };
    vi.spyOn(useBackfill, "useBackfillJobs").mockReturnValue(
      makeQuery({
        data: [pausedJob],
        meta: { total: 1, offset: 0, limit: 20 },
      }) as ReturnType<typeof useBackfill.useBackfillJobs>,
    );
    vi.spyOn(useBackfill, "useConnectors").mockReturnValue(
      makeQuery({ data: [SAMPLE_CONNECTOR], meta: {} }) as ReturnType<typeof useBackfill.useConnectors>,
    );
    render();
    expect(container.querySelector(`[data-testid="resume-btn-${pausedJob.id}"]`)).not.toBeNull();
    // No pause button for paused job
    expect(container.querySelector(`[data-testid="pause-btn-${pausedJob.id}"]`)).toBeNull();
  });

  it("hides pause button when connector is offline", () => {
    const offlineConnector = { ...SAMPLE_CONNECTOR, state: "error" };
    vi.spyOn(useBackfill, "useBackfillJobs").mockReturnValue(
      makeQuery({
        data: [SAMPLE_JOB],
        meta: { total: 1, offset: 0, limit: 20 },
      }) as ReturnType<typeof useBackfill.useBackfillJobs>,
    );
    vi.spyOn(useBackfill, "useConnectors").mockReturnValue(
      makeQuery({ data: [offlineConnector], meta: {} }) as ReturnType<typeof useBackfill.useConnectors>,
    );
    render();
    // Pause requires online; should not be shown
    expect(container.querySelector(`[data-testid="pause-btn-${SAMPLE_JOB.id}"]`)).toBeNull();
    // Cancel does not require online; still shown
    expect(container.querySelector(`[data-testid="cancel-btn-${SAMPLE_JOB.id}"]`)).not.toBeNull();
  });

  it("hides all action buttons for completed job", () => {
    const completedJob = { ...SAMPLE_JOB, status: "completed" as const };
    vi.spyOn(useBackfill, "useBackfillJobs").mockReturnValue(
      makeQuery({
        data: [completedJob],
        meta: { total: 1, offset: 0, limit: 20 },
      }) as ReturnType<typeof useBackfill.useBackfillJobs>,
    );
    render();
    expect(container.querySelector(`[data-testid="pause-btn-${completedJob.id}"]`)).toBeNull();
    expect(container.querySelector(`[data-testid="cancel-btn-${completedJob.id}"]`)).toBeNull();
    expect(container.querySelector(`[data-testid="resume-btn-${completedJob.id}"]`)).toBeNull();
  });

  it("calls pause mutation when pause button clicked", () => {
    const pauseMutation = makeMutation();
    vi.spyOn(useBackfill, "usePauseBackfillJob").mockReturnValue(
      pauseMutation as unknown as ReturnType<typeof useBackfill.usePauseBackfillJob>,
    );
    vi.spyOn(useBackfill, "useBackfillJobs").mockReturnValue(
      makeQuery({
        data: [SAMPLE_JOB],
        meta: { total: 1, offset: 0, limit: 20 },
      }) as ReturnType<typeof useBackfill.useBackfillJobs>,
    );
    vi.spyOn(useBackfill, "useConnectors").mockReturnValue(
      makeQuery({ data: [SAMPLE_CONNECTOR], meta: {} }) as ReturnType<typeof useBackfill.useConnectors>,
    );
    render();

    const pauseBtn = container.querySelector(
      `[data-testid="pause-btn-${SAMPLE_JOB.id}"]`,
    ) as HTMLButtonElement;
    act(() => { pauseBtn.click(); });
    expect(pauseMutation.mutate).toHaveBeenCalledWith(SAMPLE_JOB.id);
  });

  it("calls cancel mutation when cancel button clicked", () => {
    const cancelMutation = makeMutation();
    vi.spyOn(useBackfill, "useCancelBackfillJob").mockReturnValue(
      cancelMutation as unknown as ReturnType<typeof useBackfill.useCancelBackfillJob>,
    );
    vi.spyOn(useBackfill, "useBackfillJobs").mockReturnValue(
      makeQuery({
        data: [SAMPLE_JOB],
        meta: { total: 1, offset: 0, limit: 20 },
      }) as ReturnType<typeof useBackfill.useBackfillJobs>,
    );
    vi.spyOn(useBackfill, "useConnectors").mockReturnValue(
      makeQuery({ data: [SAMPLE_CONNECTOR], meta: {} }) as ReturnType<typeof useBackfill.useConnectors>,
    );
    render();

    const cancelBtn = container.querySelector(
      `[data-testid="cancel-btn-${SAMPLE_JOB.id}"]`,
    ) as HTMLButtonElement;
    act(() => { cancelBtn.click(); });
    expect(cancelMutation.mutate).toHaveBeenCalledWith(SAMPLE_JOB.id);
  });

  it("opens create dialog when New Backfill Job clicked", () => {
    render();
    const btn = container.querySelector('[data-testid="create-backfill-btn"]') as HTMLButtonElement;
    act(() => { btn.click(); });
    // Dialog should appear
    const dialog = document.querySelector('[role="dialog"]');
    expect(dialog).not.toBeNull();
  });

  it("shows error state when jobs query fails", () => {
    vi.spyOn(useBackfill, "useBackfillJobs").mockReturnValue(
      { data: undefined, isLoading: false, error: new Error("network error") } as unknown as ReturnType<typeof useBackfill.useBackfillJobs>,
    );
    render();
    expect(container.textContent).toContain("Failed to load backfill jobs");
  });
});
