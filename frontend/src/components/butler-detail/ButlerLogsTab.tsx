// ---------------------------------------------------------------------------
// ButlerLogsTab — bu-iuol4.17
//
// Raw log tail for any butler detail page (resident-mode tab).
//
// Layout:
//   Row 1: full-width Panel — title="raw log" sub="tail -f · auto-scroll"
//   Row 2: filter chips — ALL / DEBUG / INFO / WARN / ERROR
//   Row 3: log line list — 78px mono ts, 56px level (tonal), flex msg
//
// Hook: useButlerLogs(name, level?, limit=200) — polls every 5 s.
// Auto-scroll: enabled by default; paused when user scrolls up (toggle visible).
//
// Doctrine:
//   - No raw oklch / hex — design tokens only.
//   - Sentence case strings.
//   - tabular-nums on timestamp column (font-mono + tnum).
//   - <Time precision="ms"> for log timestamps.
// ---------------------------------------------------------------------------

import { useCallback, useEffect, useRef, useState } from "react";

import type { LogLevel } from "@/api/types";
import { Panel } from "@/components/butler-detail/atoms";
import { Skeleton } from "@/components/ui/skeleton";
import { Time } from "@/components/ui/time";
import { useButlerLogs } from "@/hooks/use-butler-logs";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Level chip definitions
// ---------------------------------------------------------------------------

type FilterLevel = "ALL" | LogLevel;

interface LevelChipDef {
  label: string;
  level: FilterLevel;
}

const LEVEL_CHIPS: LevelChipDef[] = [
  { label: "All", level: "ALL" },
  { label: "Info", level: "INFO" },
  { label: "Debug", level: "DEBUG" },
  { label: "Warn", level: "WARN" },
  { label: "Error", level: "ERROR" },
];

// ---------------------------------------------------------------------------
// Level → Tailwind token color mapping
// Doctrine: NO raw oklch/hex. Only design tokens.
// ---------------------------------------------------------------------------

function levelClass(level: LogLevel): string {
  switch (level) {
    case "DEBUG": return "text-muted-foreground";
    case "INFO":  return "text-primary";
    case "WARN":  return "text-amber-500";
    case "ERROR": return "text-destructive";
  }
}

// ---------------------------------------------------------------------------
// Filter chip sub-component
// ---------------------------------------------------------------------------

interface FilterChipProps {
  label: string;
  active: boolean;
  onClick: () => void;
}

function FilterChip({ label, active, onClick }: FilterChipProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "rounded px-2.5 py-0.5 text-xs font-mono transition-colors",
        active
          ? "bg-foreground text-background"
          : "bg-muted text-muted-foreground hover:bg-muted/70 hover:text-foreground",
      )}
      aria-pressed={active}
      data-testid="filter-chip"
    >
      {label}
    </button>
  );
}

// ---------------------------------------------------------------------------
// Log line row sub-component
// ---------------------------------------------------------------------------

interface LogLineRowProps {
  ts: string;
  level: LogLevel;
  msg: string;
}

function LogLineRow({ ts, level, msg }: LogLineRowProps) {
  return (
    <li
      className="flex min-w-0 items-baseline gap-3 py-0.5 hover:bg-muted/30"
      data-testid="log-line-row"
    >
      {/* Timestamp: fixed 78px, mono, tabular-nums */}
      <span
        className="w-[78px] shrink-0 font-mono text-[10px] tabular-nums text-muted-foreground"
        data-testid="log-ts"
      >
        <Time value={ts} mode="absolute" precision="ms" />
      </span>

      {/* Level: fixed 56px, tonal mono */}
      <span
        className={cn(
          "w-[56px] shrink-0 font-mono text-[10px] font-semibold uppercase tracking-wide tabular-nums",
          levelClass(level),
        )}
        data-testid="log-level"
      >
        {level}
      </span>

      {/* Message: flex, breaks on long lines */}
      <span
        className="min-w-0 flex-1 font-mono text-[10px] leading-relaxed break-all text-foreground"
        data-testid="log-msg"
      >
        {msg}
      </span>
    </li>
  );
}

// ---------------------------------------------------------------------------
// Auto-scroll hook
//
// Scrolls a container ref to the bottom whenever `deps` change (new log
// lines arrive), unless the user has manually scrolled up.
// ---------------------------------------------------------------------------

function useAutoScroll(enabled: boolean, deps: unknown[]) {
  const containerRef = useRef<HTMLUListElement | null>(null);
  const userScrolledUp = useRef(false);

  const scrollToBottom = useCallback(() => {
    const el = containerRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, []);

  // Detect manual upward scroll.
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    function onScroll() {
      if (!el) return;
      const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 8;
      userScrolledUp.current = !atBottom;
    }
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => el.removeEventListener("scroll", onScroll);
  }, []);

  // Scroll on new data if auto-scroll is on and user hasn't scrolled up.
  // deps is an opaque array (log lines) passed from the parent; spreading it
  // here is intentional and exhaustive-deps cannot statically verify it.
  useEffect(() => {
    if (enabled && !userScrolledUp.current) {
      scrollToBottom();
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, enabled]);

  return containerRef;
}

// ---------------------------------------------------------------------------
// ButlerLogsTab — entry point
// ---------------------------------------------------------------------------

const LOG_LIMIT = 200;

interface ButlerLogsTabProps {
  butlerName: string;
}

export default function ButlerLogsTab({ butlerName }: ButlerLogsTabProps) {
  const [filterLevel, setFilterLevel] = useState<FilterLevel>("ALL");
  const [autoScroll, setAutoScroll] = useState(true);

  const level: LogLevel | undefined = filterLevel === "ALL" ? undefined : filterLevel;

  const { data, isLoading, isError } = useButlerLogs(butlerName, {
    level,
    limit: LOG_LIMIT,
  });

  const lines = data?.lines ?? [];

  const listRef = useAutoScroll(autoScroll, [lines]);

  return (
    <div className="space-y-0 pt-4" data-testid="butler-logs-tab">
      {/* Row 1: full-width panel header */}
      <Panel title="raw log" sub="tail -f · auto-scroll" span={4} className="border-none pb-2">
        {/* Auto-scroll toggle — visible in the panel body */}
        <div className="flex items-center gap-2">
          <span className="text-xs text-muted-foreground">Auto-scroll</span>
          <button
            type="button"
            onClick={() => setAutoScroll((v) => !v)}
            className={cn(
              "rounded px-2 py-0.5 text-xs font-mono transition-colors",
              autoScroll
                ? "bg-foreground text-background"
                : "bg-muted text-muted-foreground hover:bg-muted/70 hover:text-foreground",
            )}
            aria-pressed={autoScroll}
            data-testid="auto-scroll-toggle"
          >
            {autoScroll ? "on" : "paused"}
          </button>
        </div>
      </Panel>

      {/* Row 2: filter chips */}
      <div className="flex flex-wrap items-center gap-1.5 px-4 pb-3" data-testid="filter-chips">
        {LEVEL_CHIPS.map((chip) => (
          <FilterChip
            key={chip.level}
            label={chip.label}
            active={filterLevel === chip.level}
            onClick={() => setFilterLevel(chip.level)}
          />
        ))}
      </div>

      {/* Error state */}
      {isError && (
        <p className="px-4 pb-2 text-xs text-destructive" data-testid="logs-load-error">
          Failed to load logs. Retrying...
        </p>
      )}

      {/* Row 3: log line list */}
      {isLoading && lines.length === 0 ? (
        <div className="space-y-1 px-4" data-testid="logs-loading">
          {Array.from({ length: 8 }, (_, i) => (
            <div key={i} className="flex items-center gap-3" data-testid="loading-line">
              <Skeleton className="h-3 w-[78px] rounded" />
              <Skeleton className="h-3 w-[56px] rounded" />
              <Skeleton className="h-3 flex-1 rounded" />
            </div>
          ))}
        </div>
      ) : lines.length === 0 ? (
        <p className="px-4 text-xs text-muted-foreground" data-testid="empty-state-line">
          No logs yet.
        </p>
      ) : (
        <ul
          ref={listRef}
          className="max-h-[calc(100vh-22rem)] overflow-y-auto px-4"
          aria-label="Log lines"
          data-testid="log-line-list"
        >
          {lines.map((line, idx) => (
            <LogLineRow
              // ts+level+idx is the best stable key without a dedicated line ID
              key={`${line.ts}-${line.level}-${idx}`}
              ts={line.ts}
              level={line.level}
              msg={line.msg}
            />
          ))}
        </ul>
      )}
    </div>
  );
}
