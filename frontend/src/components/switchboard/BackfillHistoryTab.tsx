/**
 * BackfillHistoryTab — History tab content for the Ingestion page.
 *
 * Features:
 * - Paginated job list with status badges, progress, and cost telemetry
 * - Create backfill job form with connector selection and date range input
 * - Pause / Cancel / Resume lifecycle buttons per job with state machine gating
 * - Progress polling (5 s) for active/pending jobs; slow polling otherwise
 * - Connector liveness/capability gating: buttons disabled when connector offline
 */

import { useState } from "react";
import { formatDistanceToNow, parseISO } from "date-fns";
import { AlertCircle, ChevronDown, ChevronUp, Loader2, PlayCircle, Plus, PauseCircle, XCircle } from "lucide-react";

import type { BackfillJobSummary, ConnectorEntry } from "@/api/types.ts";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  useBackfillJobProgress,
  useBackfillJobs,
  useCancelBackfillJob,
  useConnectors,
  useCreateBackfillJob,
  usePauseBackfillJob,
  useResumeBackfillJob,
} from "@/hooks/use-backfill";

// ---------------------------------------------------------------------------
// State machine helpers
// ---------------------------------------------------------------------------

const PAUSABLE_STATUSES = new Set(["pending", "active"]);
const CANCELLABLE_STATUSES = new Set(["pending", "active", "paused", "cost_capped", "error"]);
const RESUMABLE_STATUSES = new Set(["paused"]);
const ACTIVE_STATUSES = new Set(["pending", "active"]);

function isConnectorOnline(
  connectors: ConnectorEntry[],
  connectorType: string,
  endpointIdentity: string,
): boolean {
  const match = connectors.find(
    (c) => c.connector_type === connectorType && c.endpoint_identity === endpointIdentity,
  );
  return match?.state === "healthy";
}

// ---------------------------------------------------------------------------
// Status badge
// ---------------------------------------------------------------------------

const STATUS_VARIANTS: Record<
  string,
  "default" | "secondary" | "destructive" | "outline"
> = {
  pending: "secondary",
  active: "default",
  paused: "outline",
  completed: "secondary",
  cancelled: "outline",
  cost_capped: "destructive",
  error: "destructive",
};

function StatusBadge({ status }: { status: string }) {
  const variant = STATUS_VARIANTS[status] ?? "outline";
  return <Badge variant={variant}>{status.replace("_", " ")}</Badge>;
}

// ---------------------------------------------------------------------------
// Progress row — polls when active
// ---------------------------------------------------------------------------

interface ProgressRowProps {
  job: BackfillJobSummary;
  connectors: ConnectorEntry[];
  onPause: (id: string) => void;
  onCancel: (id: string) => void;
  onResume: (id: string) => void;
  isMutating: boolean;
}

function JobProgressRow({
  job,
  connectors,
  onPause,
  onCancel,
  onResume,
  isMutating,
}: ProgressRowProps) {
  const [expanded, setExpanded] = useState(false);

  // Poll progress for active/pending jobs
  const { data: progressData } = useBackfillJobProgress(
    ACTIVE_STATUSES.has(job.status) ? job.id : null,
    job.status,
  );

  const liveJob = progressData?.data ?? job;
  const online = isConnectorOnline(connectors, job.connector_type, job.endpoint_identity);

  const canPause = PAUSABLE_STATUSES.has(liveJob.status) && online;
  const canCancel = CANCELLABLE_STATUSES.has(liveJob.status);
  const canResume = RESUMABLE_STATUSES.has(liveJob.status) && online;

  const costDisplay =
    liveJob.cost_spent_cents > 0
      ? `$${(liveJob.cost_spent_cents / 100).toFixed(2)}`
      : "$0.00";
  const capDisplay = `$${(liveJob.daily_cost_cap_cents / 100).toFixed(2)}`;

  return (
    <>
      <TableRow
        className="cursor-pointer hover:bg-muted/50"
        onClick={() => setExpanded((v) => !v)}
        data-testid={`job-row-${job.id}`}
      >
        <TableCell className="font-mono text-xs text-muted-foreground truncate max-w-[8rem]">
          {job.id.slice(0, 8)}
        </TableCell>
        <TableCell className="font-medium">{job.connector_type}</TableCell>
        <TableCell className="text-xs text-muted-foreground">{job.endpoint_identity}</TableCell>
        <TableCell>
          <StatusBadge status={liveJob.status} />
          {ACTIVE_STATUSES.has(liveJob.status) && (
            <Loader2 className="ml-1 inline-block h-3 w-3 animate-spin text-muted-foreground" />
          )}
        </TableCell>
        <TableCell className="text-right tabular-nums">
          {liveJob.rows_processed.toLocaleString()}
        </TableCell>
        <TableCell className="text-right tabular-nums text-xs">
          {costDisplay} / {capDisplay}
        </TableCell>
        <TableCell className="text-xs text-muted-foreground">
          {job.created_at
            ? formatDistanceToNow(parseISO(job.created_at), { addSuffix: true })
            : "-"}
        </TableCell>
        <TableCell className="text-right">
          <div className="flex items-center justify-end gap-1" onClick={(e) => e.stopPropagation()}>
            {canPause && (
              <Button
                size="sm"
                variant="outline"
                disabled={isMutating}
                onClick={() => onPause(job.id)}
                data-testid={`pause-btn-${job.id}`}
                title="Pause job"
              >
                <PauseCircle className="h-4 w-4" />
              </Button>
            )}
            {canResume && (
              <Button
                size="sm"
                variant="outline"
                disabled={isMutating || !online}
                onClick={() => onResume(job.id)}
                data-testid={`resume-btn-${job.id}`}
                title={online ? "Resume job" : "Connector offline — cannot resume"}
              >
                <PlayCircle className="h-4 w-4" />
              </Button>
            )}
            {canCancel && (
              <Button
                size="sm"
                variant="outline"
                disabled={isMutating}
                onClick={() => onCancel(job.id)}
                data-testid={`cancel-btn-${job.id}`}
                title="Cancel job"
              >
                <XCircle className="h-4 w-4 text-destructive" />
              </Button>
            )}
          </div>
          <button
            className="ml-2 text-muted-foreground hover:text-foreground"
            aria-label={expanded ? "Collapse" : "Expand"}
          >
            {expanded ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
          </button>
        </TableCell>
      </TableRow>

      {expanded && (
        <TableRow className="bg-muted/30" data-testid={`job-detail-${job.id}`}>
          <TableCell colSpan={8} className="py-3 px-4">
            <div className="grid grid-cols-2 gap-x-8 gap-y-1 text-xs">
              <div>
                <span className="text-muted-foreground">Date range:</span>{" "}
                {job.date_from} — {job.date_to}
              </div>
              <div>
                <span className="text-muted-foreground">Rate limit:</span>{" "}
                {job.rate_limit_per_hour.toLocaleString()} req/hr
              </div>
              <div>
                <span className="text-muted-foreground">Rows skipped:</span>{" "}
                {liveJob.rows_skipped.toLocaleString()}
              </div>
              <div>
                <span className="text-muted-foreground">Categories:</span>{" "}
                {job.target_categories.length > 0 ? job.target_categories.join(", ") : "all"}
              </div>
              {liveJob.started_at && (
                <div>
                  <span className="text-muted-foreground">Started:</span>{" "}
                  {formatDistanceToNow(parseISO(liveJob.started_at), { addSuffix: true })}
                </div>
              )}
              {liveJob.completed_at && (
                <div>
                  <span className="text-muted-foreground">Completed:</span>{" "}
                  {formatDistanceToNow(parseISO(liveJob.completed_at), { addSuffix: true })}
                </div>
              )}
              {liveJob.error && (
                <div className="col-span-2 flex items-start gap-1 text-destructive">
                  <AlertCircle className="mt-0.5 h-3 w-3 shrink-0" />
                  {liveJob.error}
                </div>
              )}
              {!online && (
                <div className="col-span-2 flex items-center gap-1 text-muted-foreground">
                  <AlertCircle className="h-3 w-3" />
                  Connector is offline. Start/resume actions are disabled.
                </div>
              )}
            </div>
          </TableCell>
        </TableRow>
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// Create job dialog
// ---------------------------------------------------------------------------

interface CreateJobDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  connectors: ConnectorEntry[];
}

function CreateJobDialog({ open, onOpenChange, connectors }: CreateJobDialogProps) {
  const createJob = useCreateBackfillJob();

  const [connectorType, setConnectorType] = useState("");
  const [endpointIdentity, setEndpointIdentity] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [rateLimitPerHour, setRateLimitPerHour] = useState("100");
  const [dailyCostCapCents, setDailyCostCapCents] = useState("500");
  const [targetCategories, setTargetCategories] = useState("");
  const [error, setError] = useState<string | null>(null);

  const onlineConnectors = connectors.filter((c) => c.state === "healthy");

  function reset() {
    setConnectorType("");
    setEndpointIdentity("");
    setDateFrom("");
    setDateTo("");
    setRateLimitPerHour("100");
    setDailyCostCapCents("500");
    setTargetCategories("");
    setError(null);
  }

  function handleClose(open: boolean) {
    if (!open) reset();
    onOpenChange(open);
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);

    if (!connectorType || !endpointIdentity) {
      setError("Connector type and endpoint identity are required.");
      return;
    }
    if (!dateFrom || !dateTo) {
      setError("Date range is required.");
      return;
    }
    if (dateFrom > dateTo) {
      setError("Date from must be before date to.");
      return;
    }

    try {
      await createJob.mutateAsync({
        connector_type: connectorType,
        endpoint_identity: endpointIdentity,
        date_from: dateFrom,
        date_to: dateTo,
        rate_limit_per_hour: parseInt(rateLimitPerHour, 10) || 100,
        daily_cost_cap_cents: parseInt(dailyCostCapCents, 10) || 500,
        target_categories: targetCategories
          ? targetCategories.split(",").map((s) => s.trim()).filter(Boolean)
          : [],
      });
      handleClose(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create backfill job.");
    }
  }

  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Create Backfill Job</DialogTitle>
          <DialogDescription>
            Start a historical replay for a connector. Only online connectors are listed.
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={handleSubmit} className="space-y-4">
          {/* Connector selector */}
          <div className="space-y-2">
            <Label htmlFor="connector-select">Connector</Label>
            {onlineConnectors.length > 0 ? (
              <select
                id="connector-select"
                className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                value={`${connectorType}|${endpointIdentity}`}
                onChange={(e) => {
                  const [ct, ei] = e.target.value.split("|");
                  setConnectorType(ct ?? "");
                  setEndpointIdentity(ei ?? "");
                }}
                data-testid="connector-select"
              >
                <option value="|">Select a connector…</option>
                {onlineConnectors.map((c) => (
                  <option
                    key={`${c.connector_type}|${c.endpoint_identity}`}
                    value={`${c.connector_type}|${c.endpoint_identity}`}
                  >
                    {c.connector_type} — {c.endpoint_identity}
                  </option>
                ))}
              </select>
            ) : (
              <div className="flex items-center gap-2 rounded-md border border-input bg-muted/30 px-3 py-2 text-sm text-muted-foreground">
                <AlertCircle className="h-4 w-4" />
                No online connectors available.
              </div>
            )}
          </div>

          {/* Manual override when no connectors listed */}
          {onlineConnectors.length === 0 && (
            <>
              <div className="space-y-2">
                <Label htmlFor="connector-type">Connector Type</Label>
                <Input
                  id="connector-type"
                  value={connectorType}
                  onChange={(e) => setConnectorType(e.target.value)}
                  placeholder="e.g. gmail"
                  data-testid="connector-type-input"
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="endpoint-identity">Endpoint Identity</Label>
                <Input
                  id="endpoint-identity"
                  value={endpointIdentity}
                  onChange={(e) => setEndpointIdentity(e.target.value)}
                  placeholder="e.g. user@example.com"
                  data-testid="endpoint-identity-input"
                />
              </div>
            </>
          )}

          {/* Date range */}
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-2">
              <Label htmlFor="date-from">From</Label>
              <Input
                id="date-from"
                type="date"
                value={dateFrom}
                onChange={(e) => setDateFrom(e.target.value)}
                data-testid="date-from-input"
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="date-to">To</Label>
              <Input
                id="date-to"
                type="date"
                value={dateTo}
                onChange={(e) => setDateTo(e.target.value)}
                data-testid="date-to-input"
              />
            </div>
          </div>

          {/* Rate limits */}
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-2">
              <Label htmlFor="rate-limit">Rate limit (req/hr)</Label>
              <Input
                id="rate-limit"
                type="number"
                min="1"
                value={rateLimitPerHour}
                onChange={(e) => setRateLimitPerHour(e.target.value)}
                data-testid="rate-limit-input"
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="cost-cap">Daily cost cap ($)</Label>
              <Input
                id="cost-cap"
                type="number"
                min="0"
                step="0.01"
                value={(parseInt(dailyCostCapCents, 10) / 100).toFixed(2)}
                onChange={(e) =>
                  setDailyCostCapCents(String(Math.round(parseFloat(e.target.value) * 100)))
                }
                data-testid="cost-cap-input"
              />
            </div>
          </div>

          {/* Target categories */}
          <div className="space-y-2">
            <Label htmlFor="target-categories">
              Target categories{" "}
              <span className="text-muted-foreground font-normal">(comma-separated, optional)</span>
            </Label>
            <Input
              id="target-categories"
              value={targetCategories}
              onChange={(e) => setTargetCategories(e.target.value)}
              placeholder="e.g. email, contacts"
              data-testid="target-categories-input"
            />
          </div>

          {error && (
            <div className="flex items-start gap-2 rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">
              <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
              {error}
            </div>
          )}

          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => handleClose(false)}>
              Cancel
            </Button>
            <Button
              type="submit"
              disabled={createJob.isPending || onlineConnectors.length === 0}
              data-testid="create-job-submit"
            >
              {createJob.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              Create Job
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// Skeleton rows
// ---------------------------------------------------------------------------

function SkeletonRows({ count = 5 }: { count?: number }) {
  return (
    <>
      {Array.from({ length: count }, (_, i) => (
        <TableRow key={i}>
          {Array.from({ length: 8 }, (_, j) => (
            <TableCell key={j}>
              <Skeleton className="h-4 w-full" />
            </TableCell>
          ))}
        </TableRow>
      ))}
    </>
  );
}

// ---------------------------------------------------------------------------
// BackfillHistoryTab
// ---------------------------------------------------------------------------

export function BackfillHistoryTab() {
  const [createOpen, setCreateOpen] = useState(false);
  const [page, setPage] = useState(0);
  const pageSize = 20;

  const { data: jobsData, isLoading: jobsLoading, error: jobsError } = useBackfillJobs(
    { offset: page * pageSize, limit: pageSize },
    // Poll every 10 s when the list is visible (slower than individual job polling)
    { refetchInterval: 10_000 },
  );

  const { data: connectorsData } = useConnectors();

  const pause = usePauseBackfillJob();
  const cancel = useCancelBackfillJob();
  const resume = useResumeBackfillJob();

  const isMutating = pause.isPending || cancel.isPending || resume.isPending;

  const jobs = jobsData?.data ?? [];
  const connectors = connectorsData?.data ?? [];
  const total = jobsData?.meta?.total ?? 0;
  const totalPages = Math.ceil(total / pageSize);

  return (
    <div className="space-y-4" data-testid="backfill-history-tab">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold">Backfill History</h2>
          <p className="text-sm text-muted-foreground">
            Historical replay jobs — start, track, pause, or cancel per-connector backfills.
          </p>
        </div>
        <Button onClick={() => setCreateOpen(true)} data-testid="create-backfill-btn">
          <Plus className="mr-2 h-4 w-4" />
          New Backfill Job
        </Button>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Jobs</CardTitle>
          <CardDescription>
            {total > 0 ? `${total} job${total !== 1 ? "s" : ""}` : "No jobs yet"}
          </CardDescription>
        </CardHeader>
        <CardContent className="p-0">
          {jobsError ? (
            <div className="flex items-center gap-2 p-6 text-sm text-destructive">
              <AlertCircle className="h-4 w-4" />
              Failed to load backfill jobs. Check your connection and try again.
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-24">ID</TableHead>
                  <TableHead>Connector</TableHead>
                  <TableHead>Endpoint</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="text-right">Rows</TableHead>
                  <TableHead className="text-right">Cost / Cap</TableHead>
                  <TableHead>Created</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {jobsLoading ? (
                  <SkeletonRows />
                ) : jobs.length === 0 ? (
                  <TableRow>
                    <TableCell
                      colSpan={8}
                      className="py-12 text-center text-sm text-muted-foreground"
                    >
                      No backfill jobs found. Click "New Backfill Job" to get started.
                    </TableCell>
                  </TableRow>
                ) : (
                  jobs.map((job) => (
                    <JobProgressRow
                      key={job.id}
                      job={job}
                      connectors={connectors}
                      onPause={(id) => pause.mutate(id)}
                      onCancel={(id) => cancel.mutate(id)}
                      onResume={(id) => resume.mutate(id)}
                      isMutating={isMutating}
                    />
                  ))
                )}
              </TableBody>
            </Table>
          )}

          {/* Pagination */}
          {totalPages > 1 && (
            <div className="flex items-center justify-between border-t px-4 py-3 text-sm">
              <span className="text-muted-foreground">
                Page {page + 1} of {totalPages}
              </span>
              <div className="flex gap-2">
                <Button
                  size="sm"
                  variant="outline"
                  disabled={page === 0}
                  onClick={() => setPage((p) => p - 1)}
                  data-testid="prev-page-btn"
                >
                  Previous
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={page >= totalPages - 1}
                  onClick={() => setPage((p) => p + 1)}
                  data-testid="next-page-btn"
                >
                  Next
                </Button>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      <CreateJobDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        connectors={connectors}
      />
    </div>
  );
}
