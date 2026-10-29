// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { BackgroundJobsSection } from "@/components/system/BackgroundJobsSection";

let result: Record<string, unknown> = {};
vi.mock("@/hooks/use-system", () => ({ useBackgroundJobs: () => result }));
vi.mock("@/components/ui/time", () => ({ Time: ({ value }: { value: string }) => <time>{value}</time> }));

const states = [
  ["starting", "Waiting for first work"], ["ok", "Work completed"],
  ["degraded", "Reconnecting"], ["error", "Work failed"],
  ["disabled", "Disabled"], ["unknown", "No current evidence"], ["stale", "Work overdue"],
] as const;

describe("Background work", () => {
  it.each(states)("renders %s from actual work evidence", (state, label) => {
    result = { data: { data: { jobs_available: true, jobs: [{
      name: "secrets_lifecycle", job_class: "periodic", state, reason: "tick_ok",
      last_success_at: null, last_progress_at: null, error_count: 2,
      consecutive_failures: 0, transitions: [{
        state: "error", reason: "partial_failure", occurred_at: "2026-10-29T00:00:00Z",
      }],
    }] } } };
    const html = renderToStaticMarkup(<BackgroundJobsSection />);
    expect(html).toContain(label);
    expect(html).toContain("Not yet recorded");
    expect(html).toContain("2 recorded failures");
    expect(html).toContain("Recent transitions (1)");
    expect(html).toContain("partial failure");
  });

  it.each([
    [{ isPending: true }, "Loading background work."],
    [{ isError: true }, "Could not load background work."],
    [{ data: { data: { jobs_available: false, jobs: [] } } }, "Background work unavailable."],
    [{ data: { data: { jobs_available: true, jobs: [] } } }, "No background jobs registered."],
  ])("distinguishes loading, error, unavailable and empty", (data, label) => {
    result = data;
    const html = renderToStaticMarkup(<BackgroundJobsSection />);
    expect(html).toContain(label);
    expect(html).not.toContain("Work completed");
  });

  it("uses verified connection progress for continuous jobs", () => {
    result = { data: { data: { jobs_available: true, jobs: [{
      name: "fleet_events_bridge", job_class: "continuous", state: "ok", reason: "connected",
      last_success_at: null, last_progress_at: "2026-10-29T00:00:00Z", error_count: 0,
      consecutive_failures: 0, transitions: [],
    }] } } };
    const html = renderToStaticMarkup(<BackgroundJobsSection />);
    expect(html).toContain("Connected");
    expect(html).toContain("Last connection check:");
    expect(html).toContain("2026-10-29T00:00:00Z");
    expect(html).not.toContain("Last successful tick");
  });
});
