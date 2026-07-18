// ---------------------------------------------------------------------------
// CalendarVerdictOpener — JARVIS pursuit move 9, slice 4 (bu-vyjoi)
// ---------------------------------------------------------------------------

import type { ConflictIssue } from "@/api/types";
import { DispatchVerdict, type VerdictClause } from "@/components/ui/dispatch-verdict";

export interface CalendarVerdictOpenerProps {
  entriesCount: number;
  sourceCount: number;
  rangeLabel: string;
  workspaceLoading: boolean;
  workspaceError: boolean;
  sourceFreshnessLoading: boolean;
  sourceFreshnessError: boolean;
  freshnessDetail: string | null;
  conflictScanEnabled: boolean;
  conflictLoading: boolean;
  conflictError: boolean;
  conflictsAvailable: boolean;
  conflicts: ConflictIssue[];
}

function plural(count: number, singular: string, pluralWord = `${singular}s`): string {
  return `${count} ${count === 1 ? singular : pluralWord}`;
}

function buildClauses({ freshnessDetail, conflicts }: Pick<CalendarVerdictOpenerProps, "freshnessDetail" | "conflicts">): VerdictClause[] {
  const clauses: VerdictClause[] = [];

  if (freshnessDetail) {
    clauses.push({ key: "sync-freshness", text: `calendar sync ${freshnessDetail}` });
  }
  if (conflicts.length > 0) {
    clauses.push({
      key: "scheduling-conflicts",
      text: `${plural(conflicts.length, "scheduling conflict")} in view`,
    });
  }

  return clauses;
}

export function CalendarVerdictOpener({
  entriesCount,
  sourceCount,
  rangeLabel,
  workspaceLoading,
  workspaceError,
  sourceFreshnessLoading,
  sourceFreshnessError,
  freshnessDetail,
  conflictScanEnabled,
  conflictLoading,
  conflictError,
  conflictsAvailable,
  conflicts,
}: CalendarVerdictOpenerProps) {
  const sources = [
    { label: "calendar workspace", isLoading: workspaceLoading, isError: workspaceError },
    {
      label: "calendar source freshness",
      isLoading: sourceFreshnessLoading,
      isError: sourceFreshnessError,
    },
    ...(conflictScanEnabled
      ? [
          {
            label: "calendar conflict scan",
            isLoading: conflictLoading,
            isError: conflictError || !conflictsAvailable,
          },
        ]
      : []),
  ];

  const calmConflictText = conflictScanEnabled ? ", no scheduling conflicts" : "";
  return (
    <DispatchVerdict
      testId="calendar"
      landmarkLabel="Calendar verdict"
      sources={sources}
      clauses={buildClauses({ freshnessDetail, conflicts })}
      allClear={`Quiet ${rangeLabel}: ${plural(entriesCount, "event")} across ${plural(sourceCount, "source")}${calmConflictText}`}
      className="border-b border-border/60 pb-3"
    />
  );
}
