import { useMemo, useState } from "react";
import { useSearchParams } from "react-router";
import { toast } from "sonner";

import IssuesPanel from "@/components/issues/IssuesPanel";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Page } from "@/components/ui/page";
import { useForceButlerTick, usePingButler } from "@/hooks/use-butlers";
import {
  useDismissIssue,
  useIssueOccurrences,
  useIssues,
  useUndismissIssue,
} from "@/hooks/use-issues";
import { useRegisterCommands, type PaletteCommand } from "@/lib/command-registry";
import type { Issue } from "@/api/types";

export default function IssuesPage() {
  const [showDismissed, setShowDismissed] = useState(false);
  const [searchParams, setSearchParams] = useSearchParams();
  const { data, isLoading, isError } = useIssues(showDismissed);
  const dismiss = useDismissIssue();
  const undismiss = useUndismissIssue();
  const pingButler = usePingButler();
  const runNow = useForceButlerTick();
  const allIssues = useMemo(() => data?.data ?? [], [data]);

  // ?q= deep-link (JARVIS audit move 6): a failure row on the Audit Log page
  // links here with the first line of its error text so a failure is one hop
  // from "root evidence" to "its issue group" without the frontend
  // reconstructing the backend's lossy grouping slug. This is an honest
  // substring match over the currently-loaded feed, not a precise group
  // lookup — the closest exact match is usually the top (only) result.
  const qFilter = (searchParams.get("q") ?? "").trim();
  const issues = useMemo(() => {
    if (!qFilter) return allIssues;
    const needle = qFilter.toLowerCase();
    return allIssues.filter((issue) => (issue.error_message ?? issue.description).toLowerCase().includes(needle));
  }, [allIssues, qFilter]);

  function handleClearQFilter() {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      next.delete("q");
      return next;
    });
  }

  // Occurrences drill-down (JARVIS audit move 6, slice 3): at most one issue
  // group's occurrences are fetched at a time, keyed by which row is
  // expanded. Lives here (not in IssuesPanel) so the panel stays a plain
  // presentational component with no data-fetching of its own, consistent
  // with how dismiss/ping/run-now are already wired through props.
  const [expandedIssueKey, setExpandedIssueKey] = useState<string | null>(null);
  const occurrencesQuery = useIssueOccurrences(expandedIssueKey, expandedIssueKey !== null);

  function handleToggleOccurrences(issueKey: string) {
    setExpandedIssueKey((prev) => (prev === issueKey ? null : issueKey));
  }

  function handleDismiss(issue: Issue) {
    dismiss.mutate({ issueKey: issue.issue_key, lastSeenAt: issue.last_seen_at });
  }

  // Ping butler (JARVIS audit move 6, bu-86c4c.15): a real live MCP ping via
  // GET /api/butlers/{name}. HONEST-PENDING -- the owner sees the real
  // reachability result once the round trip settles, not a faked instant one.
  function handlePingButler(name: string) {
    pingButler.mutate(name, {
      onSuccess: (response) => {
        if (response.data.status === "online") {
          toast.success(`${name} is reachable`);
        } else {
          toast.error(`${name} is still unreachable`);
        }
      },
      onError: (err) => {
        toast.error(`Failed to ping ${name}`, {
          description: err instanceof Error ? err.message : undefined,
        });
      },
    });
  }

  // Run schedule now (JARVIS audit move 6, bu-86c4c.15): forces the butler's
  // scheduler to run any due schedules immediately via POST
  // /api/butlers/{name}/tick. HONEST-PENDING -- a real dispatch, not
  // reversible, so no optimistic apply.
  function handleRunScheduleNow(name: string) {
    runNow.mutate(name, {
      onSuccess: (response) => {
        if (response.data.success) {
          toast.success(
            response.data.message ? `${name}: ${response.data.message}` : `${name}: schedule run now`,
          );
        } else {
          toast.error(`${name}: tick did not complete successfully`);
        }
      },
      onError: (err) => {
        toast.error(`Failed to run schedule for ${name}`, {
          description: err instanceof Error ? err.message : undefined,
        });
      },
    });
  }

  // -------------------------------------------------------------------
  // Command menu Actions (bu-86c4c.7 — per-page command registration API).
  // "Acknowledge issue" acks the newest active issue — acknowledgment is
  // this page's existing triage mechanism (acknowledge-until-recurrence, not
  // a separate permanent state), so the command reuses it rather than
  // inventing a parallel one.
  // -------------------------------------------------------------------
  const commandMenuCommands = useMemo<PaletteCommand[]>(() => {
    if (showDismissed || issues.length === 0) return [];
    const nextIssue = issues[0];
    return [
      {
        id: "acknowledge-issue",
        label: "Acknowledge issue",
        keywords: ["dismiss", "issue"],
        perform: () =>
          dismiss.mutate({ issueKey: nextIssue.issue_key, lastSeenAt: nextIssue.last_seen_at }),
      },
    ];
  }, [showDismissed, issues, dismiss]);
  useRegisterCommands(commandMenuCommands);

  return (
    <Page
      archetype="list"
      title="Issues"
      description={
        showDismissed
          ? "Acknowledged issues. They reappear automatically if they recur, or restore one now."
          : "Grouped errors and warnings across all butlers, newest first."
      }
      actions={
        <Button
          variant="outline"
          size="sm"
          onClick={() => setShowDismissed((prev) => !prev)}
        >
          {showDismissed ? "Show active" : "Show acknowledged"}
        </Button>
      }
    >
      {qFilter && (
        <div className="flex flex-wrap items-center gap-2" data-testid="q-filter">
          <Badge
            variant="secondary"
            className="gap-1.5 py-1 pl-2.5 pr-1.5 text-xs"
            data-testid="q-filter-chip"
          >
            search: {qFilter}
            <button
              type="button"
              aria-label={`Clear search filter ${qFilter}`}
              className="hover:text-foreground text-muted-foreground ml-0.5 rounded-sm text-xs leading-none"
              onClick={handleClearQFilter}
            >
              &times;
            </button>
          </Badge>
        </div>
      )}
      <IssuesPanel
        issues={issues}
        isLoading={isLoading}
        isError={isError}
        dismissedView={showDismissed}
        onDismiss={handleDismiss}
        isDismissing={dismiss.isPending}
        onRestore={(issueKey) => undismiss.mutate(issueKey)}
        isRestoring={undismiss.isPending}
        onPingButler={handlePingButler}
        pendingPingButler={pingButler.isPending ? pingButler.variables : null}
        onRunScheduleNow={handleRunScheduleNow}
        pendingRunNowButler={runNow.isPending ? runNow.variables : null}
        expandedIssueKey={expandedIssueKey}
        onToggleOccurrences={handleToggleOccurrences}
        occurrences={occurrencesQuery.data?.data ?? []}
        occurrencesLoading={occurrencesQuery.isLoading}
        occurrencesError={occurrencesQuery.isError}
      />
    </Page>
  );
}
