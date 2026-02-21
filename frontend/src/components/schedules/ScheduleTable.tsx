import { formatDistanceToNow, format } from "date-fns";

import type { Schedule, ScheduleDispatchMode } from "@/api/types.ts";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface ScheduleTableProps {
  schedules: Schedule[];
  isLoading: boolean;
  onToggle: (schedule: Schedule) => void;
  onEdit: (schedule: Schedule) => void;
  onDelete: (schedule: Schedule) => void;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Truncate text to a maximum length, appending an ellipsis if needed. */
function truncate(text: string, max = 80): string {
  if (text.length <= max) return text;
  return text.slice(0, max) + "\u2026";
}

function resolveDispatchMode(schedule: Schedule): ScheduleDispatchMode {
  if (schedule.dispatch_mode === "job" || schedule.dispatch_mode === "prompt") {
    return schedule.dispatch_mode;
  }
  return schedule.job_name ? "job" : "prompt";
}

function formatJobArgsPreview(jobArgs: Schedule["job_args"]): string {
  if (!jobArgs) return "";
  try {
    return JSON.stringify(jobArgs);
  } catch {
    return "";
  }
}

/** Format an ISO timestamp as relative + absolute tooltip. */
function formatTimestamp(iso: string | null): { relative: string; absolute: string } | null {
  if (!iso) return null;
  const date = new Date(iso);
  const relative = formatDistanceToNow(date, { addSuffix: true });
  const absolute = format(date, "MMM d, h:mm a");
  return { relative, absolute };
}

// ---------------------------------------------------------------------------
// Skeleton rows
// ---------------------------------------------------------------------------

function SkeletonRows({ count = 5 }: { count?: number }) {
  return (
    <>
      {Array.from({ length: count }, (_, i) => (
        <TableRow key={i}>
          <TableCell><Skeleton className="h-4 w-24" /></TableCell>
          <TableCell><Skeleton className="h-4 w-20" /></TableCell>
          <TableCell><Skeleton className="h-4 w-14" /></TableCell>
          <TableCell><Skeleton className="h-4 w-48" /></TableCell>
          <TableCell><Skeleton className="h-4 w-14" /></TableCell>
          <TableCell><Skeleton className="h-4 w-12" /></TableCell>
          <TableCell><Skeleton className="h-4 w-20" /></TableCell>
          <TableCell><Skeleton className="h-4 w-20" /></TableCell>
          <TableCell className="text-right"><Skeleton className="h-4 w-20 ml-auto" /></TableCell>
        </TableRow>
      ))}
    </>
  );
}

// ---------------------------------------------------------------------------
// Empty state
// ---------------------------------------------------------------------------

function EmptyState() {
  return (
    <div className="text-muted-foreground flex flex-col items-center justify-center py-12 text-sm">
      <p>No schedules found.</p>
      <p className="mt-1 text-xs">Create one to get started.</p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// ScheduleTable
// ---------------------------------------------------------------------------

export function ScheduleTable({
  schedules,
  isLoading,
  onToggle,
  onEdit,
  onDelete,
}: ScheduleTableProps) {
  if (!isLoading && schedules.length === 0) {
    return <EmptyState />;
  }

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Name</TableHead>
          <TableHead>Cron</TableHead>
          <TableHead>Mode</TableHead>
          <TableHead>Prompt / Job</TableHead>
          <TableHead>Enabled</TableHead>
          <TableHead>Source</TableHead>
          <TableHead>Next Run</TableHead>
          <TableHead>Last Run</TableHead>
          <TableHead className="text-right">Actions</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {isLoading ? (
          <SkeletonRows />
        ) : (
          schedules.map((schedule) => {
            const nextRun = formatTimestamp(schedule.next_run_at);
            const lastRun = formatTimestamp(schedule.last_run_at);
            const dispatchMode = resolveDispatchMode(schedule);
            const promptText = schedule.prompt?.trim() ?? "";
            const jobArgsPreview = formatJobArgsPreview(schedule.job_args);

            return (
              <TableRow key={schedule.id}>
                <TableCell className="font-medium">{schedule.name}</TableCell>
                <TableCell>
                  <code className="rounded bg-muted px-1.5 py-0.5 text-xs">
                    {schedule.cron}
                  </code>
                </TableCell>
                <TableCell>
                  {dispatchMode === "prompt" ? (
                    <Badge variant="secondary">prompt</Badge>
                  ) : (
                    <Badge className="bg-blue-600 text-white hover:bg-blue-600/90">job</Badge>
                  )}
                </TableCell>
                <TableCell className="max-w-xs text-sm">
                  {dispatchMode === "prompt" ? (
                    <p className="text-muted-foreground" title={promptText}>
                      {promptText ? truncate(promptText) : "\u2014"}
                    </p>
                  ) : (
                    <div className="space-y-1">
                      <p className="font-mono text-xs" title={schedule.job_name ?? ""}>
                        {schedule.job_name?.trim() ? schedule.job_name : "\u2014"}
                      </p>
                      {jobArgsPreview ? (
                        <p className="text-muted-foreground text-xs" title={jobArgsPreview}>
                          {truncate(jobArgsPreview)}
                        </p>
                      ) : (
                        <p className="text-muted-foreground text-xs">\u2014</p>
                      )}
                    </div>
                  )}
                </TableCell>
                <TableCell>
                  <button
                    type="button"
                    onClick={() => onToggle(schedule)}
                    className="cursor-pointer"
                    title={schedule.enabled ? "Click to disable" : "Click to enable"}
                  >
                    {schedule.enabled ? (
                      <Badge className="bg-emerald-600 text-white hover:bg-emerald-600/90">
                        On
                      </Badge>
                    ) : (
                      <Badge variant="secondary">Off</Badge>
                    )}
                  </button>
                </TableCell>
                <TableCell className="text-xs text-muted-foreground">
                  {schedule.source}
                </TableCell>
                <TableCell
                  className="text-xs text-muted-foreground"
                  title={nextRun?.absolute}
                >
                  {nextRun?.relative ?? "\u2014"}
                </TableCell>
                <TableCell
                  className="text-xs text-muted-foreground"
                  title={lastRun?.absolute}
                >
                  {lastRun?.relative ?? "\u2014"}
                </TableCell>
                <TableCell className="text-right">
                  <div className="flex justify-end gap-1">
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => onEdit(schedule)}
                    >
                      Edit
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      className="text-destructive hover:bg-destructive/10"
                      onClick={() => onDelete(schedule)}
                    >
                      Delete
                    </Button>
                  </div>
                </TableCell>
              </TableRow>
            );
          })
        )}
      </TableBody>
    </Table>
  );
}

export default ScheduleTable;
