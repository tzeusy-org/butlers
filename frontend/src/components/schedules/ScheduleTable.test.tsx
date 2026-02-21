import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";

import type { Schedule } from "@/api/types.ts";
import { ScheduleTable } from "@/components/schedules/ScheduleTable";

const noop = vi.fn();

function renderTable(schedules: Schedule[]): string {
  return renderToStaticMarkup(
    <ScheduleTable
      schedules={schedules}
      isLoading={false}
      onToggle={noop}
      onEdit={noop}
      onDelete={noop}
    />,
  );
}

function makeSchedule(overrides: Partial<Schedule>): Schedule {
  return {
    id: "sched-1",
    name: "daily-review",
    cron: "0 9 * * *",
    prompt: "Run morning digest",
    source: "toml",
    enabled: true,
    next_run_at: null,
    last_run_at: null,
    created_at: "2026-02-21T00:00:00Z",
    updated_at: "2026-02-21T00:00:00Z",
    ...overrides,
  };
}

describe("ScheduleTable dual-mode rendering", () => {
  it("renders prompt and job mode rows with mode visibility", () => {
    const html = renderTable([
      makeSchedule({
        id: "sched-prompt",
        dispatch_mode: "prompt",
        prompt: "Run morning digest",
      }),
      makeSchedule({
        id: "sched-job",
        name: "eligibility-sweep",
        dispatch_mode: "job",
        prompt: null,
        job_name: "switchboard.eligibility_sweep",
        job_args: { policy_tier: "default" },
      }),
    ]);

    expect(html).toContain("prompt");
    expect(html).toContain("job");
    expect(html).toContain("Run morning digest");
    expect(html).toContain("switchboard.eligibility_sweep");
    expect(html).toContain("{&quot;policy_tier&quot;:&quot;default&quot;}");
  });

  it("falls back to job mode when legacy rows omit dispatch_mode but include job_name", () => {
    const html = renderTable([
      makeSchedule({
        id: "sched-fallback",
        name: "stats-rollup",
        dispatch_mode: null,
        prompt: null,
        job_name: "switchboard.connector_stats_rollup",
      }),
    ]);

    expect(html).toContain("job");
    expect(html).toContain("switchboard.connector_stats_rollup");
  });
});
