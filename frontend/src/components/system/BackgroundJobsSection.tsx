import { Section, SectionContent, SectionHeader, SectionTitle } from "@/components/ui/Section";
import { Time } from "@/components/ui/time";
import { useBackgroundJobs } from "@/hooks/use-system";

const labels = {
  starting: "Waiting for first work",
  ok: "Work completed",
  degraded: "Reconnecting",
  error: "Work failed",
  disabled: "Disabled",
  unknown: "No current evidence",
  stale: "Work overdue",
} as const;

/** A rule-separated work ledger, independent from process and fleet liveness. */
export function BackgroundJobsSection() {
  const q = useBackgroundJobs();
  return (
    <Section>
      <SectionHeader><SectionTitle>Background work</SectionTitle></SectionHeader>
      <SectionContent>
        {q.isPending ? <p role="status">Loading background work.</p> : q.isError ? (
          <p role="alert">Could not load background work.</p>
        ) : !q.data?.data.jobs_available ? (
          <p role="status">Background work unavailable. Current health could not be read.</p>
        ) : q.data.data.jobs.length === 0 ? (
          <p className="font-serif italic text-muted-foreground">No background jobs registered.</p>
        ) : (
          <ul className="divide-y divide-border">
            {q.data.data.jobs.map((job) => (
              <li key={job.name} className="py-3">
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <span className="font-mono text-sm">{job.name.replaceAll("_", " ")}</span>
                  <span className="text-sm">{job.state === "ok" && job.job_class === "continuous"
                    ? "Connected" : labels[job.state]}</span>
                </div>
                <p className="text-xs text-muted-foreground">
                  {job.job_class === "continuous" ? "Last connection check: " : "Last successful tick: "}
                  {(job.job_class === "continuous" ? job.last_progress_at : job.last_success_at)
                    ? <Time value={(job.job_class === "continuous" ? job.last_progress_at : job.last_success_at)!} />
                    : "Not yet recorded"}
                  {job.error_count > 0 && ` · ${job.error_count} recorded failures`}
                </p>
                {job.transitions.length > 0 && (
                  <details className="mt-1 text-xs">
                    <summary className="cursor-pointer">Recent transitions ({job.transitions.length})</summary>
                    <ul className="mt-2 space-y-1 text-muted-foreground">
                      {job.transitions.map((event, index) => (
                        <li key={`${event.occurred_at}-${index}`}>
                          <Time value={event.occurred_at} /> · {event.reason.replaceAll("_", " ")}
                        </li>
                      ))}
                    </ul>
                  </details>
                )}
              </li>
            ))}
          </ul>
        )}
      </SectionContent>
    </Section>
  );
}
