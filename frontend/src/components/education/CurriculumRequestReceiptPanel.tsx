import { Link } from "react-router";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { SourceDegradedNote } from "@/components/ui/query-boundary";
import { Time } from "@/components/ui/time";
import { useCurriculumRequestReceipt } from "@/hooks/use-education";
import type { CurriculumRequestReceipt } from "@/api/index.ts";

/**
 * Accepted-to-outcome receipt for a curriculum request (bu-6jv4m.10).
 *
 * The old flow announced "the butler will set it up within a few minutes and
 * message you to begin" the instant the API returned 202. The API had only
 * written a lock and spawned a detached task whose trigger failure was caught
 * and logged, so nothing could ever falsify that promise. This panel renders
 * the durable receipt instead: acceptance is acceptance, completion is claimed
 * only from a terminal receipt, a failure is named with its reason, and an
 * unreadable receipt store is shown as unavailable rather than as calm.
 */

interface CurriculumRequestReceiptPanelProps {
  /** The `request_id` from the 202, or null to track the most recent request. */
  requestId: string | null;
  /** Opens the correlated curriculum once the receipt names one. */
  onOpenCurriculum?: (mindMapId: string) => void;
  /** Reopens the request dialog after a terminal failure. */
  onRetry?: () => void;
}

/**
 * Owner-facing copy for each terminal failure reason the backend can settle.
 * An unrecognised reason falls through to a generic line that still refuses to
 * claim success, so a new backend reason degrades to honest, not to silent.
 */
const FAILURE_COPY: Record<string, string> = {
  trigger_unreachable:
    "The butler could not be reached, so no session ran. Nothing was set up.",
  session_error:
    "The session reported an error before a curriculum was created.",
  no_curriculum_created:
    "The session finished without creating a curriculum.",
  timed_out:
    "The request timed out without settling. The butler may have restarted mid-request.",
};

/**
 * How long a settled receipt stays on the page in fallback mode. With no
 * tracked `request_id` the panel reads the LATEST request, so without this a
 * curriculum created weeks ago would keep its card forever. A tracked request
 * (the one just submitted in this session) is never hidden.
 */
const RECENT_SETTLED_MS = 60 * 60 * 1000;

function isLongSettled(receipt: CurriculumRequestReceipt): boolean {
  if (!receipt.settled_at) return false;
  const settled = Date.parse(receipt.settled_at);
  return Number.isFinite(settled) && Date.now() - settled > RECENT_SETTLED_MS;
}

function failureCopy(reason: string | null | undefined): string {
  if (reason && reason in FAILURE_COPY) return FAILURE_COPY[reason];
  return "The request failed before a curriculum was created.";
}

function SessionDoor({ sessionId }: { sessionId: string }) {
  return (
    <Button asChild variant="outline" size="sm">
      <Link to={`/sessions/${encodeURIComponent(sessionId)}`}>View session</Link>
    </Button>
  );
}

function ReceiptShell({
  badge,
  testId,
  children,
}: {
  badge: React.ReactNode;
  testId: string;
  children: React.ReactNode;
}) {
  return (
    <Card data-testid="curriculum-receipt">
      <CardHeader className="flex flex-row items-center justify-between gap-2">
        <CardTitle>Curriculum request</CardTitle>
        {badge}
      </CardHeader>
      <CardContent>
        <div
          role="status"
          aria-live="polite"
          className="space-y-3"
          data-testid={testId}
        >
          {children}
        </div>
      </CardContent>
    </Card>
  );
}

export default function CurriculumRequestReceiptPanel({
  requestId,
  onOpenCurriculum,
  onRetry,
}: CurriculumRequestReceiptPanelProps) {
  const { data, isError, isLoading, refetch } = useCurriculumRequestReceipt(requestId);

  // An unreadable store is not an empty one. Rendering "nothing in flight"
  // here would be fabricated calm: the request may well be running.
  if (isError || (data && !data.receipts_available)) {
    return (
      <Card data-testid="curriculum-receipt">
        <CardHeader>
          <CardTitle>Curriculum request</CardTitle>
        </CardHeader>
        <CardContent>
          <SourceDegradedNote
            label="Curriculum request status"
            detail="unavailable, so an in-flight request cannot be shown"
            onRetry={() => void refetch()}
            testId="curriculum-receipt-unavailable"
          />
        </CardContent>
      </Card>
    );
  }

  if (isLoading || !data) return null;

  const receipt: CurriculumRequestReceipt | null = data.receipt;
  if (!receipt) return null;
  if (!requestId && isLongSettled(receipt)) return null;

  if (receipt.status === "completed") {
    return (
      <ReceiptShell
        badge={<Badge variant="outline">Ready</Badge>}
        testId="curriculum-receipt-completed"
      >
        <p className="text-sm">
          The curriculum for <span className="font-medium">{receipt.topic}</span> was created{" "}
          <Time value={receipt.settled_at ?? receipt.updated_at} mode="relative" />.
        </p>
        <p className="text-xs text-muted-foreground">
          {receipt.calibration_ready_at
            ? "Calibration started, so the butler is assessing your level."
            : "Calibration has not started yet."}
        </p>
        <div className="flex flex-wrap gap-2">
          {receipt.mind_map_id && onOpenCurriculum && (
            <Button
              size="sm"
              onClick={() => onOpenCurriculum(receipt.mind_map_id as string)}
            >
              Open curriculum
            </Button>
          )}
          {receipt.session_id && <SessionDoor sessionId={receipt.session_id} />}
        </div>
      </ReceiptShell>
    );
  }

  if (receipt.status === "failed") {
    return (
      <ReceiptShell
        badge={<Badge variant="destructive">Failed</Badge>}
        testId="curriculum-receipt-failed"
      >
        <p className="text-sm">
          The request for <span className="font-medium">{receipt.topic}</span> did not complete.
        </p>
        <p className="text-sm text-destructive">{failureCopy(receipt.failure_reason)}</p>
        <div className="flex flex-wrap gap-2">
          {onRetry && (
            <Button size="sm" onClick={onRetry}>
              Request again
            </Button>
          )}
          {/* The session door stays on the failure path: a failed session is
              exactly the evidence worth reading. */}
          {receipt.session_id && <SessionDoor sessionId={receipt.session_id} />}
        </div>
      </ReceiptShell>
    );
  }

  const running = receipt.status === "running";
  return (
    <ReceiptShell
      badge={<Badge variant="outline">{running ? "Working" : "Accepted"}</Badge>}
      testId={running ? "curriculum-receipt-running" : "curriculum-receipt-accepted"}
    >
      <p className="text-sm">
        Your request for <span className="font-medium">{receipt.topic}</span> was accepted{" "}
        <Time value={receipt.requested_at} mode="relative" />.{" "}
        {running
          ? "A session is building the curriculum now."
          : "It has not started yet."}
      </p>
      <p className="text-xs text-muted-foreground">
        Nothing is set up until this panel says so. It updates on its own while
        the request is in flight.
      </p>
      {receipt.session_id && (
        <div className="flex flex-wrap gap-2">
          <SessionDoor sessionId={receipt.session_id} />
        </div>
      )}
    </ReceiptShell>
  );
}
