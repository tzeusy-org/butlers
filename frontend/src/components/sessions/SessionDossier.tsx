import { useEffect, useState } from "react";
import { Link } from "react-router";
import { CheckIcon, CopyIcon } from "lucide-react";

import { Time } from "@/components/ui/time";
import { ComplexityBadge } from "@/components/general/ComplexityBadge";
import { cn } from "@/lib/utils";
import type { SessionDetail } from "@/api/types.ts";
import { CollapsibleJson, ToolCallTimeline } from "./ToolCallTimeline";
import { formatDurationMs } from "@/lib/format-duration";

// ---------------------------------------------------------------------------
// SessionDossier — the ONE session dossier on the trace spine
// (bu-qvnce.5, pursuit move 5).
//
// Renders EVERY SessionDetail field the store holds. Extracted from
// SessionDetailDrawer so the drawer (butler-scoped, opened from a list) and
// SessionDetailPage (global, deep-linked from 10+ surfaces) share one body —
// previously the page silently omitted trace_id/request_id/parent_session_id/
// process_log despite the store carrying all of them (SessionDetailPage.tsx:
// 134-263 vs types.ts:221-268).
//
// Evidence links: trace_id -> the ingestion timeline pre-filtered to that
// trace (the trace drill-down spine, bu-86c4c.3 — genuinely backend-filtered,
// unlike a `/timeline` deep link, which has no trace_id predicate on
// GET /api/timeline today); request_id -> /sessions?request=, the existing
// SessionsPage request-id filter; parent_session_id -> the parent's own
// dossier. process_log.stderr/exit_code are surfaced as the named root
// evidence for failures instead of being dropped entirely (the drawer used to
// show only process_log.runtime_type).
// ---------------------------------------------------------------------------

export interface SessionDossierProps {
  session: SessionDetail;
  className?: string;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatTokens(n: number | null): string {
  if (n == null) return "—";
  return n.toLocaleString();
}

/** True while a session has not yet reached a terminal state (StatusBadge's own convention). */
function isSessionRunning(session: SessionDetail): boolean {
  return session.success === null;
}

/**
 * Compute the current elapsed ms outside the React render path, mirroring
 * time.tsx's formatClock24h/resolveSmartMode convention: a plain helper
 * (not a hook) is the one place that reads Date.now(), so the
 * react-hooks/purity rule's direct-call check does not flag it. The hook
 * below only re-renders on a tick counter; it never holds the elapsed value
 * itself in state.
 */
function computeElapsedMs(startedAt: string): number | null {
  const startedMs = Date.parse(startedAt);
  if (Number.isNaN(startedMs)) return null;
  return Math.max(Date.now() - startedMs, 0);
}

/**
 * Live "Xm Ys" elapsed label for a running session (JARVIS pursuit move 5,
 * slice 3 — the page must not freeze on "Running" for the session's whole
 * lifetime). Ticks every second while running; frozen once terminal.
 */
function useElapsedLabel(startedAt: string, isRunning: boolean): string | null {
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (!isRunning) return;
    const id = setInterval(() => setTick((n) => n + 1), 1000);
    return () => clearInterval(id);
  }, [isRunning]);

  if (!isRunning) return null;
  void tick; // consumed only to trigger a re-render each second; value unused
  const elapsedMs = computeElapsedMs(startedAt);
  if (elapsedMs == null) return null;
  return formatDurationMs(elapsedMs);
}

// ---------------------------------------------------------------------------
// Copyable text
// ---------------------------------------------------------------------------

function CopyableText({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);

  function handleCopy() {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }

  return (
    <button
      type="button"
      onClick={handleCopy}
      className="inline-flex items-center gap-1 rounded px-1 py-0.5 text-xs font-mono text-muted-foreground hover:bg-muted transition-colors"
      title="Copy to clipboard"
    >
      <span className="truncate max-w-[200px]">{text}</span>
      {copied ? (
        <CheckIcon className="size-3 text-[var(--green)] shrink-0" />
      ) : (
        <CopyIcon className="size-3 shrink-0" />
      )}
    </button>
  );
}

// ---------------------------------------------------------------------------
// Metadata grid row
// ---------------------------------------------------------------------------

function MetadataRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-4 py-1.5 border-b border-border/50 last:border-0">
      <span className="text-xs font-medium text-muted-foreground shrink-0">{label}</span>
      <span className="text-xs text-right">{children}</span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Collapsible plain-text block (stderr) — CollapsibleJson's sibling for raw
// text, since JSON.stringify-ing a stderr string would mangle its newlines.
// ---------------------------------------------------------------------------

function CollapsibleText({ label, text }: { label: string; text: string }) {
  const [open, setOpen] = useState(false);

  return (
    <div className="border rounded-md">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="flex items-center gap-1 w-full px-2 py-1.5 text-xs font-medium text-left hover:bg-muted/50 transition-colors"
        aria-expanded={open}
      >
        {label}
      </button>
      {open && (
        <pre className="px-2 pb-2 text-xs overflow-x-auto whitespace-pre-wrap break-words text-destructive max-h-48">
          {text}
        </pre>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// SessionDossier
// ---------------------------------------------------------------------------

export function SessionDossier({ session, className }: SessionDossierProps) {
  const running = isSessionRunning(session);
  const elapsed = useElapsedLabel(session.started_at, running);

  // process_log stderr/exit_code as the named root evidence for failures —
  // only shown for a session that actually failed (success === false); a
  // running or successful session's process_log (if any) is uninteresting
  // evidence and stays out of the way.
  const hasRootEvidence =
    session.success === false &&
    session.process_log != null &&
    (session.process_log.exit_code != null || !!session.process_log.stderr);

  return (
    <div className={cn("flex flex-col gap-5", className)}>
      {/* Metadata */}
      <section>
        <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-2">
          Metadata
        </h3>
        <div className="rounded-md border p-3">
          <MetadataRow label="Butler">
            <Link
              to={`/butlers/${encodeURIComponent(session.butler)}`}
              className="text-primary underline-offset-4 hover:underline"
            >
              {session.butler}
            </Link>
          </MetadataRow>
          <MetadataRow label="Trigger">{session.trigger_source}</MetadataRow>
          <MetadataRow label="Started">
            {session.started_at ? (
              <Time value={session.started_at} mode="absolute" precision="second" />
            ) : (
              "—"
            )}
          </MetadataRow>
          <MetadataRow label="Completed">
            {session.completed_at ? (
              <Time value={session.completed_at} mode="absolute" precision="second" />
            ) : (
              "—"
            )}
          </MetadataRow>
          {running ? (
            <MetadataRow label="Elapsed">
              <span className="font-mono tabular-nums" data-testid="session-elapsed">
                {elapsed ?? "—"}
              </span>
            </MetadataRow>
          ) : (
            <MetadataRow label="Duration">{formatDurationMs(session.duration_ms)}</MetadataRow>
          )}
          <MetadataRow label="Model">
            <span className="font-mono">{session.model ?? "—"}</span>
          </MetadataRow>
          {session.resolution_source && (
            <MetadataRow label="Resolution Source">
              <span className="text-muted-foreground">{session.resolution_source}</span>
            </MetadataRow>
          )}
          {session.complexity && (
            <MetadataRow label="Complexity">
              <ComplexityBadge tier={session.complexity} />
            </MetadataRow>
          )}
          {session.process_log?.runtime_type && (
            <MetadataRow label="Runtime Type">
              <span className="text-muted-foreground">{session.process_log.runtime_type}</span>
            </MetadataRow>
          )}
        </div>
      </section>

      {/* Prompt */}
      <section>
        <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-2">
          Prompt
        </h3>
        <pre className="rounded-md border p-3 text-xs whitespace-pre-wrap break-words max-h-48 overflow-y-auto bg-muted/30">
          {session.prompt}
        </pre>
      </section>

      {/* Tool calls */}
      <section>
        <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-2">
          Tool Calls ({session.tool_calls.length})
        </h3>
        <ToolCallTimeline toolCalls={session.tool_calls} />
      </section>

      {/* Result */}
      {session.result != null && (
        <section>
          <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-2">
            Result
          </h3>
          <pre className="rounded-md border p-3 text-xs whitespace-pre-wrap break-words max-h-48 overflow-y-auto bg-muted/30">
            {session.result}
          </pre>
        </section>
      )}

      {/* Error + root evidence */}
      {session.error != null && (
        <section>
          <h3 className="text-xs font-semibold uppercase tracking-wide text-destructive mb-2">
            Error
          </h3>
          <pre
            className={cn(
              "rounded-md border border-destructive/30 bg-destructive/5 p-3 text-xs",
              "whitespace-pre-wrap break-words max-h-48 overflow-y-auto text-destructive",
            )}
          >
            {session.error}
          </pre>
          {hasRootEvidence && (
            <div className="mt-2 space-y-2" data-testid="session-root-evidence">
              <h4 className="text-[11px] font-semibold uppercase tracking-wide text-destructive">
                Root Evidence
              </h4>
              {session.process_log?.exit_code != null && (
                <MetadataRow label="Exit Code">
                  <span className="font-mono">{session.process_log.exit_code}</span>
                </MetadataRow>
              )}
              {session.process_log?.stderr && (
                <CollapsibleText label="stderr" text={session.process_log.stderr} />
              )}
            </div>
          )}
        </section>
      )}

      {/* Token breakdown */}
      <section>
        <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-2">
          Token Usage
        </h3>
        <div className="rounded-md border p-3">
          <MetadataRow label="Input Tokens">{formatTokens(session.input_tokens)}</MetadataRow>
          <MetadataRow label="Output Tokens">{formatTokens(session.output_tokens)}</MetadataRow>
          <MetadataRow label="Total">
            {session.input_tokens != null && session.output_tokens != null
              ? formatTokens(session.input_tokens + session.output_tokens)
              : "—"}
          </MetadataRow>
        </div>
      </section>

      {/* Cost */}
      {session.cost != null && Object.keys(session.cost).length > 0 && (
        <section>
          <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-2">
            Cost
          </h3>
          <CollapsibleJson label="Cost breakdown" data={session.cost} />
        </section>
      )}

      {/* Trace ID — the trace drill-down spine (bu-86c4c.3). Links to the
          ingestion timeline pre-filtered by trace_id, which is genuinely
          backend-filtered; GET /api/timeline (the fleet chronicle) has no
          trace_id predicate today, so this intentionally does not link
          there — see the bu-qvnce.5 worker report for the follow-up. */}
      {session.trace_id != null && (
        <section>
          <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-2">
            Trace ID
          </h3>
          <div className="flex items-center gap-2">
            <Link
              to={`/ingestion?trace=${encodeURIComponent(session.trace_id)}`}
              className="text-xs font-mono text-primary underline underline-offset-2 hover:text-primary/80 transition-colors truncate max-w-[200px]"
            >
              {session.trace_id}
            </Link>
            <CopyableText text={session.trace_id} />
          </div>
        </section>
      )}

      {/* Request ID — links to the sessions list pre-filtered by request_id
          (SessionsPage's existing ?request= filter). */}
      {session.request_id != null && (
        <section>
          <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-2">
            Request ID
          </h3>
          <div className="flex items-center gap-2">
            <Link
              to={`/sessions?request=${encodeURIComponent(session.request_id)}`}
              className="text-xs font-mono text-primary underline underline-offset-2 hover:text-primary/80 transition-colors truncate max-w-[200px]"
            >
              {session.request_id}
            </Link>
            <CopyableText text={session.request_id} />
          </div>
        </section>
      )}

      {/* Parent session — links directly to the parent's own dossier. */}
      {session.parent_session_id != null && (
        <section>
          <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-2">
            Parent Session
          </h3>
          <Link
            to={`/sessions/${encodeURIComponent(session.parent_session_id)}`}
            className="text-xs font-mono text-primary underline underline-offset-2 hover:text-primary/80 transition-colors truncate max-w-[200px] inline-block"
          >
            {session.parent_session_id}
          </Link>
        </section>
      )}
    </div>
  );
}
