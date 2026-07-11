import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router";
import { toast } from "sonner";

import IssuesPanel from "@/components/issues/IssuesPanel";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Page } from "@/components/ui/page";
import { ListTriageFooterHint } from "@/components/ui/list-triage-footer";
import { cn } from "@/lib/utils";
import { useForceButlerTick, usePingButler } from "@/hooks/use-butlers";
import {
  useDismissIssue,
  useIssueOccurrences,
  useIssues,
  useUndismissIssue,
} from "@/hooks/use-issues";
import { useRegisterCommands, type PaletteCommand } from "@/lib/command-registry";
import { useListTriage, type ListTriageVerb } from "@/hooks/use-list-triage";
import type { Issue } from "@/api/types";

// ---------------------------------------------------------------------------
// URL-backed window + severity/butler pills (bu-qvnce.13, pursuit move 13)
// ---------------------------------------------------------------------------

const DEFAULT_WINDOW = "7d";

const WINDOW_OPTIONS = [
  { value: "24h", label: "24h" },
  { value: "7d", label: "7d" },
  { value: "30d", label: "30d" },
  { value: "all", label: "All time" },
] as const;

const SEVERITY_OPTIONS = [
  { value: "critical", label: "Critical" },
  { value: "warning", label: "Warning" },
] as const;

function parseCsvSet(sp: URLSearchParams, key: string): Set<string> {
  return new Set((sp.get(key) ?? "").split(",").filter(Boolean));
}

function toggleInCsv(sp: URLSearchParams, key: string, value: string): void {
  const current = parseCsvSet(sp, key);
  if (current.has(value)) {
    current.delete(value);
  } else {
    current.add(value);
  }
  if (current.size > 0) {
    sp.set(key, Array.from(current).sort().join(","));
  } else {
    sp.delete(key);
  }
}

export default function IssuesPage() {
  const [showDismissed, setShowDismissed] = useState(false);
  const [searchParams, setSearchParams] = useSearchParams();

  // Time window (default 7d, capped CTE server-side — bu-qvnce.13). Shareable
  // and reloadable: the URL is the sole source of truth, no local mirror.
  const activeWindow = searchParams.get("window") || DEFAULT_WINDOW;
  const selectedSeverities = useMemo(() => parseCsvSet(searchParams, "severity"), [searchParams]);
  const selectedButlers = useMemo(() => parseCsvSet(searchParams, "butler"), [searchParams]);

  const { data, isLoading, isError } = useIssues({
    includeDismissed: showDismissed,
    window: activeWindow,
  });
  const dismiss = useDismissIssue();
  const undismiss = useUndismissIssue();
  const pingButler = usePingButler();
  const runNow = useForceButlerTick();
  const windowedIssues = useMemo(() => data?.data ?? [], [data]);

  // Degraded feed (bu-tpudw.3): this feed's product IS failure, so a backend
  // source that errored is named in `meta.sources_degraded` rather than
  // zero-filled into an all-clear empty feed. A non-empty list means the feed
  // undercounts, so IssuesPanel must NOT render its "No issues recorded."
  // all-clear — it names the dropped sources via a SourceDegradedNote instead
  // (CLAUDE.md degraded-envelope convention). Absent/empty keeps the honest
  // empty state.
  const sourcesDegraded = data?.meta?.sources_degraded ?? [];

  // Butler options are derived from the currently-loaded (windowed) feed so
  // the pill row never offers a butler with zero issues in view.
  const availableButlers = useMemo(() => {
    const names = new Set<string>();
    for (const issue of windowedIssues) {
      for (const name of issue.butlers?.length ? issue.butlers : [issue.butler]) {
        if (name) names.add(name);
      }
    }
    return Array.from(names).sort();
  }, [windowedIssues]);

  // ?q= deep-link (JARVIS audit move 6): a failure row on the Audit Log page
  // links here with the first line of its error text so a failure is one hop
  // from "root evidence" to "its issue group" without the frontend
  // reconstructing the backend's lossy grouping slug. This is an honest
  // substring match over the currently-loaded feed, not a precise group
  // lookup — the closest exact match is usually the top (only) result.
  const qFilter = (searchParams.get("q") ?? "").trim();
  const issues = useMemo(() => {
    let result = windowedIssues;
    if (qFilter) {
      const needle = qFilter.toLowerCase();
      result = result.filter((issue) =>
        (issue.error_message ?? issue.description).toLowerCase().includes(needle),
      );
    }
    if (selectedSeverities.size > 0) {
      result = result.filter((issue) => selectedSeverities.has(issue.severity.toLowerCase()));
    }
    if (selectedButlers.size > 0) {
      result = result.filter((issue) =>
        (issue.butlers?.length ? issue.butlers : [issue.butler]).some((name) =>
          selectedButlers.has(name),
        ),
      );
    }
    return result;
  }, [windowedIssues, qFilter, selectedSeverities, selectedButlers]);

  function handleClearQFilter() {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      next.delete("q");
      return next;
    });
  }

  function handleWindowChange(value: string) {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      if (value === DEFAULT_WINDOW) next.delete("window");
      else next.set("window", value);
      return next;
    });
  }

  function handleToggleSeverity(value: string) {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      toggleInCsv(next, "severity", value);
      return next;
    });
  }

  function handleToggleButler(name: string) {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      toggleInCsv(next, "butler", name);
      return next;
    });
  }

  const hasActivePills = selectedSeverities.size > 0 || selectedButlers.size > 0;

  function handleClearPills() {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      next.delete("severity");
      next.delete("butler");
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

  // j/k roving selection + a=acknowledge/restore act key over the issue rows
  // (bu-qvnce.11 slice 4 -- useListTriage, extracted from ApprovalsPage's own
  // former hand-rolled version of this exact pattern). Selection is
  // ephemeral component state, not URL-backed (bu-qvnce.13's window/severity/
  // butler/q filters already own the URL; a row cursor is not shareable
  // state the way a filter is).
  const [selectedIssueKey, setSelectedIssueKey] = useState<string | null>(null);
  const issueKeys = useMemo(() => issues.map((issue) => issue.issue_key), [issues]);
  const issueVerbs = useMemo<ListTriageVerb[]>(() => {
    const issue = issues.find((i) => i.issue_key === selectedIssueKey);
    if (!issue) return [];
    if (showDismissed) {
      return [
        {
          key: "a",
          description: "Restore selected",
          handler: () => undismiss.mutate(issue.issue_key),
        },
      ];
    }
    return [
      {
        key: "a",
        description: "Acknowledge selected",
        handler: () => dismiss.mutate({ issueKey: issue.issue_key, lastSeenAt: issue.last_seen_at }),
      },
    ];
  }, [issues, selectedIssueKey, showDismissed, dismiss, undismiss]);
  const { hints: issueTriageHints } = useListTriage({
    ids: issueKeys,
    selectedId: selectedIssueKey,
    onSelect: setSelectedIssueKey,
    verbs: issueVerbs,
  });

  // Keep DOM focus in sync with the current selection, mirroring
  // ApprovalsPage's identical rail-focus effect.
  useEffect(() => {
    if (!selectedIssueKey) return;
    const nodes = document.querySelectorAll<HTMLElement>('[data-testid="issue-row"]');
    for (const node of nodes) {
      if (node.getAttribute("data-issue-key") === selectedIssueKey) {
        node.focus({ preventScroll: true });
        break;
      }
    }
  }, [selectedIssueKey]);

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
      {/* Window + severity/butler pills (bu-qvnce.13): URL-backed, shareable.
          Window defaults to 7d server-side (capped CTE); "All time" opts out
          of the time bound but the row cap still applies. */}
      <div className="flex flex-wrap items-center gap-3" data-testid="issues-filter-bar">
        <div className="flex items-center gap-1.5" role="group" aria-label="Time window">
          {WINDOW_OPTIONS.map((opt) => (
            <button key={opt.value} type="button" onClick={() => handleWindowChange(opt.value)}>
              <Badge
                variant={activeWindow === opt.value ? "default" : "outline"}
                className="cursor-pointer"
                data-testid={`window-${opt.value}`}
              >
                {opt.label}
              </Badge>
            </button>
          ))}
        </div>

        <div className="flex items-center gap-1.5" role="group" aria-label="Severity">
          {SEVERITY_OPTIONS.map((opt) => (
            <button key={opt.value} type="button" onClick={() => handleToggleSeverity(opt.value)}>
              <Badge
                variant={selectedSeverities.has(opt.value) ? "default" : "outline"}
                className={cn(
                  "cursor-pointer",
                  selectedSeverities.has(opt.value) && "bg-primary text-primary-foreground",
                )}
                data-testid={`severity-${opt.value}`}
              >
                {opt.label}
              </Badge>
            </button>
          ))}
        </div>

        {availableButlers.length > 0 && (
          <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="Butler">
            {availableButlers.map((name) => (
              <button key={name} type="button" onClick={() => handleToggleButler(name)}>
                <Badge
                  variant={selectedButlers.has(name) ? "default" : "outline"}
                  className={cn(
                    "cursor-pointer",
                    selectedButlers.has(name) && "bg-primary text-primary-foreground",
                  )}
                >
                  {name}
                </Badge>
              </button>
            ))}
          </div>
        )}

        {hasActivePills && (
          <Button variant="ghost" size="sm" onClick={handleClearPills}>
            Clear pills
          </Button>
        )}
      </div>

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
        sourcesDegraded={sourcesDegraded}
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
        selectedIssueKey={selectedIssueKey}
      />
      {/* Shared footer hint strip (bu-qvnce.11 slice 4) -- advertises the
          EXACT j/k/a bindings useListTriage just registered. */}
      <ListTriageFooterHint bindings={issueTriageHints} />
    </Page>
  );
}
