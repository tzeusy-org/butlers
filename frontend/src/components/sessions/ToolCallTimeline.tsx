/**
 * Shared tool-call timeline component used by both SessionDetailDrawer and
 * SessionDetailPage.
 *
 * Renders an ordered list of tool calls with:
 * - Color-coded outcome indicators (green/red/amber)
 * - Expandable Arguments, Result, and Error JSON blocks
 * - Smart name extraction from nested structures
 * - Outcome inference from status fields, error payloads, etc.
 */

import { useState } from "react";
import { ChevronDownIcon, ChevronRightIcon } from "lucide-react";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Collapsible JSON block
// ---------------------------------------------------------------------------

export function CollapsibleJson({ label, data }: { label: string; data: unknown }) {
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

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

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

// ---------------------------------------------------------------------------
// Outcome inference
// ---------------------------------------------------------------------------

interface NormalizedToolCall {
  key: string;
  name: string;
  outcome: ToolCallOutcome;
  args?: unknown;
  result?: unknown;
  error?: unknown;
  raw: unknown;
}

type ToolCallOutcome = "success" | "failed" | "pending" | "unknown";

const TOOL_CALL_OUTCOME_STYLES: Record<ToolCallOutcome, { dotClass: string; label: string }> = {
  success: { dotClass: "bg-emerald-500", label: "Success" },
  failed: { dotClass: "bg-destructive", label: "Failed" },
  pending: { dotClass: "bg-amber-500", label: "Pending" },
  unknown: { dotClass: "bg-muted-foreground/40", label: "Unknown" },
};

const SUCCESS_TOOL_OUTCOME_STATUSES = new Set([
  "accepted", "acknowledged", "complete", "completed", "done",
  "executed", "ok", "sent", "success", "succeeded",
]);

const FAILED_TOOL_OUTCOME_STATUSES = new Set([
  "aborted", "cancelled", "canceled", "denied", "error", "expired",
  "failed", "failure", "rejected", "timed_out", "timeout",
]);

const PENDING_TOOL_OUTCOME_STATUSES = new Set([
  "in_progress", "partial", "pending", "processing", "queued",
  "running", "started",
]);

function hasErrorPayload(value: unknown): boolean {
  if (value == null) return false;
  if (typeof value === "string") return value.trim().length > 0;
  if (Array.isArray(value)) return value.length > 0;
  return true;
}

function normalizeOutcomeStatus(value: string): ToolCallOutcome | undefined {
  const normalized = value.trim().toLowerCase().replace(/[\s-]+/g, "_");
  if (!normalized) return undefined;
  if (FAILED_TOOL_OUTCOME_STATUSES.has(normalized) || normalized.startsWith("error")) {
    return "failed";
  }
  if (PENDING_TOOL_OUTCOME_STATUSES.has(normalized)) {
    return "pending";
  }
  if (SUCCESS_TOOL_OUTCOME_STATUSES.has(normalized)) {
    return "success";
  }
  return undefined;
}

function outcomeFromRecord(record: Record<string, unknown>): ToolCallOutcome | undefined {
  if (hasErrorPayload(record.error)) return "failed";

  for (const key of ["is_error", "isError"] as const) {
    const value = record[key];
    if (typeof value === "boolean") {
      return value ? "failed" : "success";
    }
  }

  for (const key of ["success", "ok"] as const) {
    const value = record[key];
    if (typeof value === "boolean") {
      return value ? "success" : "failed";
    }
  }

  for (const key of ["exit_code", "exitCode"] as const) {
    const value = record[key];
    if (typeof value === "number" && Number.isFinite(value)) {
      return value === 0 ? "success" : "failed";
    }
  }

  for (const key of ["status", "state", "outcome", "result_status", "resultStatus"] as const) {
    const value = record[key];
    if (typeof value === "string") {
      const mapped = normalizeOutcomeStatus(value);
      if (mapped != null) return mapped;
    }
  }

  return undefined;
}

function outcomeCandidateRecords(
  call: Record<string, unknown>,
  args: unknown,
  result: unknown,
): Record<string, unknown>[] {
  const candidates: Record<string, unknown>[] = [call, ...nestedToolCallContainers(call)];

  for (const key of ["input", "output", "response", "result", "return", "value"] as const) {
    const value = getNestedValue(call, [key]);
    if (isRecord(value)) {
      candidates.push(value);
    }
  }
  if (isRecord(args)) {
    candidates.push(args);
  }
  if (isRecord(result)) {
    candidates.push(result);
  }

  return candidates;
}

function inferToolCallOutcome(
  call: Record<string, unknown>,
  args: unknown,
  result: unknown,
): ToolCallOutcome {
  const outcomes = new Set<ToolCallOutcome>();
  for (const record of outcomeCandidateRecords(call, args, result)) {
    const outcome = outcomeFromRecord(record);
    if (outcome != null) outcomes.add(outcome);
  }

  if (outcomes.has("failed")) return "failed";
  if (outcomes.has("pending")) return "pending";
  if (outcomes.has("success")) return "success";
  return "unknown";
}

function normalizeToolCall(call: unknown, idx: number): NormalizedToolCall {
  if (!isRecord(call)) {
    return {
      key: `tool-${idx + 1}`,
      name: `Tool #${idx + 1}`,
      outcome: "unknown",
      raw: call,
    };
  }

  const name = extractToolName(call) ?? `Tool #${idx + 1}`;

  const argsRaw = getNestedValue(call, ["input", "args", "arguments", "parameters", "payload"]);
  const resultRaw = getNestedValue(call, ["result", "output", "response", "return", "value"]);
  const errorRaw = getNestedValue(call, ["error", "exception", "failure", "failure_reason"]);
  const idRaw = getNestedValue(call, ["id", "call_id", "callId"]);
  const key = typeof idRaw === "string" && idRaw.trim().length > 0 ? idRaw : `tool-${idx + 1}`;

  return {
    key,
    name,
    args: argsRaw == null ? undefined : parseJsonIfString(argsRaw),
    result: resultRaw == null ? undefined : parseJsonIfString(resultRaw),
    outcome: inferToolCallOutcome(
      call,
      argsRaw == null ? undefined : parseJsonIfString(argsRaw),
      resultRaw == null ? undefined : parseJsonIfString(resultRaw),
    ),
    error: errorRaw == null ? undefined : parseJsonIfString(errorRaw),
    raw: call,
  };
}

function extractToolNamesFromResult(result: string | null): string[] {
  if (typeof result !== "string" || result.length === 0) {
    return [];
  }

  const names: string[] = [];
  const patterns = [
    /`([A-Za-z0-9_./-]+)\(/g,
    /-\s*`([A-Za-z0-9_./-]+)`\s*:/g,
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
// ToolCallTimeline — public component
// ---------------------------------------------------------------------------

export function ToolCallTimeline({
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
        const outcomeStyle = TOOL_CALL_OUTCOME_STYLES[tc.outcome];
        return (
          <li key={`${tc.key}-${idx}`} className="ml-4">
            <span
              role="img"
              aria-label={`Tool call outcome: ${outcomeStyle.label}`}
              title={`Tool call outcome: ${outcomeStyle.label}`}
              data-tool-call-outcome={tc.outcome}
              className={cn(
                "absolute -left-1.5 mt-1 size-3 rounded-full border border-background",
                outcomeStyle.dotClass,
              )}
            />
            <p className="text-xs font-semibold">{tc.name}</p>
            {tc.outcome && (
              <p className="text-[11px] text-muted-foreground">
                Outcome:{" "}
                <span
                  className={cn(
                    "font-medium",
                    /error|fail/i.test(tc.outcome)
                      ? "text-destructive"
                      : /success|accepted|ok/i.test(tc.outcome)
                        ? "text-emerald-600"
                        : "text-muted-foreground",
                  )}
                >
                  {tc.outcome}
                </span>
              </p>
            )}
            {tc.args !== undefined && (
              <CollapsibleJson label="Arguments" data={tc.args} />
            )}
            {tc.result !== undefined && (
              <CollapsibleJson label="Result" data={tc.result} />
            )}
            {tc.error !== undefined && (
              <CollapsibleJson label="Error" data={tc.error} />
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
