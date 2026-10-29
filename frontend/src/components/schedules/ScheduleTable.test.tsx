import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";

import type { Schedule } from "@/api/types.ts";
import { ScheduleTable } from "@/components/schedules/ScheduleTable";

import { getButlerSchedules } from "@/api/client";
import { ownerFetch } from "@/api/owner-session";
vi.mock("@/api/owner-session", async (original) => ({ ...await original<typeof import("@/api/owner-session")>(), ownerFetch: vi.fn() }));
type RawSchedule = Omit<Schedule, "dispatch_mode"> & { dispatch_mode: Schedule["dispatch_mode"] | null };

const noop = vi.fn();

async function renderTable(schedules: RawSchedule[]): Promise<string> {
  vi.mocked(ownerFetch).mockResolvedValueOnce(new Response(JSON.stringify({ data: schedules, meta: {} })));
  const response = await getButlerSchedules("general");
  return renderToStaticMarkup(
    <ScheduleTable
      schedules={response.data}
      isLoading={false}
      onToggle={noop}
      onEdit={noop}
      onDelete={noop}
      onTrigger={noop}
    />,
  );
}

function makeSchedule(overrides: Partial<RawSchedule>): RawSchedule {
  return {
dispatch_mode: "prompt",
job_name: null,
job_args: null,
complexity: null,
timezone: null,
start_at: null,
end_at: null,
until_at: null,
display_title: null,
calendar_event_id: null,
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

describe("ScheduleTable dual-mode rendering", async () => {
  it("renders prompt and job mode rows with mode visibility", async () => {
    const html = await renderTable([
      makeSchedule({
        id: "sched-prompt",
        dispatch_mode: "prompt",
        prompt: "Run morning digest",
      }),
      makeSchedule({
        id: "sched-job",
        name: "eligibility_sweep",
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

  it("routes the enable/disable action through a focusable tooltip, not a native title= (bu-sywxz)", async () => {
    // The enable/disable action hint used to live only in a `title=` attribute,
    // which never surfaced on keyboard focus. It is now a radix Tip trigger.
    const html = await renderTable([
      makeSchedule({ id: "sched-on", enabled: true }),
    ]);
    // The visible On/Off state still renders...
    expect(html).toContain("On");
    // ...but the action hint no longer leaks as a native title= tooltip.
    expect(html).not.toContain('title="Click to disable"');
    expect(html).not.toContain('title="Click to enable"');
  });

  it("falls back to job mode when legacy rows omit dispatch_mode but include job_name", async () => {
    const html = await renderTable([
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
