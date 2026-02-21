import { useState } from "react";
import { Link } from "react-router";
import { format } from "date-fns";
import { CopyIcon, CheckIcon, ChevronDownIcon, ChevronRightIcon } from "lucide-react";

import { useSessionDetail } from "@/hooks/use-sessions";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
} from "@/components/ui/sheet";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface SessionDetailDrawerProps {
  butler: string;
  sessionId: string | null; // null = closed
  onClose: () => void;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatTimestamp(iso: string | null): string {
  if (!iso) return "\u2014";
  return format(new Date(iso), "MMM d, yyyy h:mm:ss a");
}

function formatDuration(ms: number | null): string {
  if (ms == null) return "\u2014";
  if (ms < 1000) return `${ms}ms`;
  const totalSeconds = Math.floor(ms / 1000);
  const frac = ms / 1000;
  if (totalSeconds < 60) {
    return frac % 1 === 0 ? `${totalSeconds}s` : `${frac.toFixed(1)}s`;
  }
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return seconds > 0 ? `${minutes}m ${seconds}s` : `${minutes}m`;
}

function formatTokens(n: number | null): string {
  if (n == null) return "\u2014";
  return n.toLocaleString();
}

function statusBadge(success: boolean | null) {
  if (success === true) {
    return (
      <Badge className="bg-emerald-600 text-white hover:bg-emerald-600/90">
        Success
      </Badge>
    );
  }
  if (success === false) {
    return <Badge variant="destructive">Failed</Badge>;
  }
  return (
    <Badge variant="outline" className="border-gray-400 text-gray-500">
      Running
    </Badge>
  );
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
        <CheckIcon className="size-3 text-emerald-500 shrink-0" />
      ) : (
        <CopyIcon className="size-3 shrink-0" />
      )}
    </button>
  );
}

// ---------------------------------------------------------------------------
// Collapsible JSON block
// ---------------------------------------------------------------------------

function CollapsibleJson({ label, data }: { label: string; data: unknown }) {
  const [open, setOpen] = useState(false);

  return (
    <div className="border rounded-md">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="flex items-center gap-1 w-full px-2 py-1.5 text-xs font-medium text-left hover:bg-muted/50 transition-colors"
      >
        {open ? (
          <ChevronDownIcon className="size-3 shrink-0" />
        ) : (
          <ChevronRightIcon className="size-3 shrink-0" />
        )}
        {label}
      </button>
      {open && (
        <pre className="px-2 pb-2 text-xs overflow-x-auto text-muted-foreground max-h-48">
          {JSON.stringify(data, null, 2)}
        </pre>
      )}
    </div>
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function parseJsonIfString(value: unknown): unknown {
  if (typeof value !== "string") return value;
  const trimmed = value.trim();
  if (!trimmed) return value;
  try {
    return JSON.parse(trimmed);
  } catch {
    return value;
  }
}

function nestedToolCallContainers(tc: Record<string, unknown>): Record<string, unknown>[] {
  const containers: Record<string, unknown>[] = [];
  for (const key of ["function", "call", "tool_call", "toolCall"]) {
    const candidate = tc[key];
    if (isRecord(candidate)) {
      containers.push(candidate);
    }
  }
  return containers;
}

function getNestedValue(
  tc: Record<string, unknown>,
  keys: string[],
): unknown {
  for (const key of keys) {
    if (key in tc) return tc[key];
  }
  for (const container of nestedToolCallContainers(tc)) {
    for (const key of keys) {
      if (key in container) return container[key];
    }
  }
  return undefined;
}

function extractToolName(value: unknown, depth = 0): string | undefined {
  if (!isRecord(value) || depth > 4) return undefined;

  for (const key of ["name", "tool", "tool_name", "toolName"]) {
    const candidate = value[key];
    if (typeof candidate === "string" && candidate.trim().length > 0) {
      return candidate;
    }
  }

  for (const key of ["function", "call", "tool", "tool_call", "toolCall"]) {
    const nestedName = extractToolName(value[key], depth + 1);
    if (nestedName != null) return nestedName;
  }

  return undefined;
}

interface NormalizedToolCall {
  key: string;
  name: string;
  args?: unknown;
  result?: unknown;
  raw: unknown;
}

function normalizeToolCall(call: unknown, idx: number): NormalizedToolCall {
  if (!isRecord(call)) {
    return {
      key: `tool-${idx + 1}`,
      name: `Tool #${idx + 1}`,
      raw: call,
    };
  }

  const name = extractToolName(call) ?? `Tool #${idx + 1}`;

  const argsRaw = getNestedValue(call, ["input", "args", "arguments", "parameters", "payload"]);
  const resultRaw = getNestedValue(call, ["result", "output", "response", "return", "value"]);
  const idRaw = getNestedValue(call, ["id", "call_id", "callId"]);
  const key = typeof idRaw === "string" && idRaw.trim().length > 0 ? idRaw : `tool-${idx + 1}`;

  return {
    key,
    name,
    args: argsRaw == null ? undefined : parseJsonIfString(argsRaw),
    result: resultRaw == null ? undefined : parseJsonIfString(resultRaw),
    raw: call,
  };
}

function extractToolNamesFromResult(result: string | null): string[] {
  if (typeof result !== "string" || result.length === 0) {
    return [];
  }

  const names: string[] = [];
  const patterns = [
    /`([A-Za-z0-9_./-]+)\(/g, // `tool_name(...)
    /-\s*`([A-Za-z0-9_./-]+)`\s*:/g, // - `tool_name`:
  ];

  for (const regex of patterns) {
    let match: RegExpExecArray | null = regex.exec(result);
    while (match != null) {
      const name = match[1];
      if (name && name.trim().length > 0) {
        names.push(name);
      }
      match = regex.exec(result);
    }
  }
  return names;
}

// ---------------------------------------------------------------------------
// Metadata grid
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
// Loading skeleton
// ---------------------------------------------------------------------------

function DrawerSkeleton() {
  return (
    <div className="space-y-4 p-4">
      <Skeleton className="h-6 w-48" />
      <div className="space-y-2">
        {Array.from({ length: 6 }, (_, i) => (
          <Skeleton key={i} className="h-4 w-full" />
        ))}
      </div>
      <Skeleton className="h-24 w-full" />
      <Skeleton className="h-16 w-full" />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tool calls timeline
// ---------------------------------------------------------------------------

function ToolCallTimeline({
  toolCalls,
  resultText,
}: {
  toolCalls: unknown[];
  resultText: string | null;
}) {
  if (toolCalls.length === 0) {
    return (
      <p className="text-xs text-muted-foreground italic">No tool calls recorded.</p>
    );
  }

  const parsedNames = extractToolNamesFromResult(resultText);
  const normalized = toolCalls.map((call, idx) => normalizeToolCall(call, idx));
  const hydrated = normalized.reduce(
    (state, call, idx) => {
      const defaultName = `Tool #${idx + 1}`;
      if (call.name !== defaultName || state.nextNameIndex >= parsedNames.length) {
        return {
          calls: [...state.calls, call],
          nextNameIndex: state.nextNameIndex,
        };
      }
      return {
        calls: [
          ...state.calls,
          {
            ...call,
            name: parsedNames[state.nextNameIndex],
          },
        ],
        nextNameIndex: state.nextNameIndex + 1,
      };
    },
    { calls: [] as NormalizedToolCall[], nextNameIndex: 0 },
  ).calls;

  return (
    <ol className="relative border-l border-border/60 ml-2 space-y-3">
      {hydrated.map((tc, idx) => {
        return (
          <li key={`${tc.key}-${idx}`} className="ml-4">
            <div className="absolute -left-1.5 mt-1 size-3 rounded-full border border-background bg-muted-foreground/40" />
            <p className="text-xs font-semibold">{tc.name}</p>
            {tc.args !== undefined && (
              <CollapsibleJson label="Arguments" data={tc.args} />
            )}
            {tc.result !== undefined && (
              <CollapsibleJson label="Result" data={tc.result} />
            )}
            {tc.args === undefined && tc.result === undefined && (
              <CollapsibleJson label="Raw Payload" data={tc.raw} />
            )}
          </li>
        );
      })}
    </ol>
  );
}

// ---------------------------------------------------------------------------
// SessionDetailDrawer
// ---------------------------------------------------------------------------

export function SessionDetailDrawer({
  butler,
  sessionId,
  onClose,
}: SessionDetailDrawerProps) {
  const { data, isLoading } = useSessionDetail(butler, sessionId);
  const session = data?.data ?? null;

  return (
    <Sheet open={sessionId != null} onOpenChange={(open) => !open && onClose()}>
      <SheetContent side="right" className="w-full sm:max-w-lg overflow-y-auto">
        {isLoading || !session ? (
          <>
            <SheetHeader>
              <SheetTitle>Session Detail</SheetTitle>
              <SheetDescription>Loading session information...</SheetDescription>
            </SheetHeader>
            <DrawerSkeleton />
          </>
        ) : (
          <>
            {/* Header */}
            <SheetHeader>
              <SheetTitle className="flex items-center gap-2 text-sm">
                <span className="font-mono truncate">{session.id}</span>
                {statusBadge(session.success)}
              </SheetTitle>
              <SheetDescription>
                {session.butler} &mdash; {session.trigger_source}
              </SheetDescription>
            </SheetHeader>

            <div className="flex flex-col gap-5 px-4 pb-6">
              {/* Metadata grid */}
              <section>
                <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-2">
                  Metadata
                </h3>
                <div className="rounded-md border p-3">
                  <MetadataRow label="Butler">{session.butler}</MetadataRow>
                  <MetadataRow label="Trigger">{session.trigger_source}</MetadataRow>
                  <MetadataRow label="Started">{formatTimestamp(session.started_at)}</MetadataRow>
                  <MetadataRow label="Completed">{formatTimestamp(session.completed_at)}</MetadataRow>
                  <MetadataRow label="Duration">{formatDuration(session.duration_ms)}</MetadataRow>
                  <MetadataRow label="Model">{session.model ?? "\u2014"}</MetadataRow>
                  {session.parent_session_id && (
                    <MetadataRow label="Parent Session">
                      <span className="font-mono text-[10px]">{session.parent_session_id}</span>
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
                <ToolCallTimeline toolCalls={session.tool_calls} resultText={session.result} />
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

              {/* Error */}
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
                </section>
              )}

              {/* Token breakdown */}
              <section>
                <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-2">
                  Token Usage
                </h3>
                <div className="rounded-md border p-3">
                  <MetadataRow label="Input Tokens">
                    {formatTokens(session.input_tokens)}
                  </MetadataRow>
                  <MetadataRow label="Output Tokens">
                    {formatTokens(session.output_tokens)}
                  </MetadataRow>
                  <MetadataRow label="Total">
                    {session.input_tokens != null && session.output_tokens != null
                      ? formatTokens(session.input_tokens + session.output_tokens)
                      : "\u2014"}
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

              {/* Trace ID */}
              {session.trace_id != null && (
                <section>
                  <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-2">
                    Trace ID
                  </h3>
                  <div className="flex items-center gap-2">
                    <Link
                      to={`/traces/${encodeURIComponent(session.trace_id)}`}
                      className="text-xs font-mono text-primary underline underline-offset-2 hover:text-primary/80 transition-colors truncate max-w-[200px]"
                    >
                      {session.trace_id}
                    </Link>
                    <CopyableText text={session.trace_id} />
                  </div>
                </section>
              )}
            </div>
          </>
        )}
      </SheetContent>
    </Sheet>
  );
}
