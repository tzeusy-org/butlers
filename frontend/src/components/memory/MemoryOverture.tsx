// ---------------------------------------------------------------------------
// MemoryOverture — Bands 1 & 2 of the /memory house-ledger (bu-2ix8d.2)
//
// The top of /memory answers "is remembering working" before any scrolling:
//   Band 1 — Overture: eyebrow, display headline, templated Voice sentence,
//            4-cell KPI strip (hairline-divided, no card chrome).
//   Band 2 — Pipeline: the lifecycle as a single line of mono numerals with
//            `─→` connectors; a right-aligned `dead letters N` fragment that
//            turns --red ONLY when dead_letter > 0.
//
// Color discipline (MEMORY_LANGUAGE.md §6): a healthy page renders zero red
// pixels above the fold. The dead-letter numeral in the pipeline band is the
// only state color this band may show, and only when its state exists.
//
// Motion (§8): numerals NEVER count up. Layout heights are reserved so the
// band does not shift as stats load.
//
// Binding docs:
// - (memory house-ledger redesign, graduated) prompts/01-overture.md
// - (memory house-ledger redesign, graduated) MEMORY_LANGUAGE.md §2, §6, §7, §8
// ---------------------------------------------------------------------------

import { MemoryLoadError } from "@/components/memory/MemoryLoadError";
import { Display } from "@/components/ui/Display";
import { Eyebrow } from "@/components/ui/Eyebrow";
import { Mono } from "@/components/ui/Mono";
import { SourceDegradedNote } from "@/components/ui/query-boundary";
import { Voice } from "@/components/ui/Voice";
import { useTimezone } from "@/components/ui/timezone-context";
import { useMemoryStats } from "@/hooks/use-memory";
import {
  composeVoiceSentence,
  formatNumeral,
  writeupCell,
} from "@/lib/memory-overture";
import { cn } from "@/lib/utils";
import type { MemoryStats } from "@/api/types.ts";

// ---------------------------------------------------------------------------
// KPI strip
// ---------------------------------------------------------------------------

/** A single hairline-divided KPI cell: mono eyebrow + mega-number. */
function KpiCell({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-2">
      <Eyebrow as="div">{label}</Eyebrow>
      {/* Reserve the mega-number row height so the strip never shifts. */}
      <div className="flex min-h-[38px] items-baseline">{children}</div>
    </div>
  );
}

/** Mega-number: 32px sans 500, tabular numerals. Never animates on load. */
function Mega({ children }: { children: React.ReactNode }) {
  return (
    <span className="font-sans text-[32px] font-medium leading-none tracking-[-0.02em] tabular-nums text-[var(--fg)]">
      {children}
    </span>
  );
}

function KpiStrip({ stats, tz }: { stats: MemoryStats; tz: string }) {
  const writeup = writeupCell(stats, tz);
  return (
    <div className="grid grid-cols-2 gap-x-8 gap-y-6 sm:grid-cols-4">
      <KpiCell label="Pending">
        <Mega>{formatNumeral(stats.unconsolidated_episodes)}</Mega>
      </KpiCell>
      <KpiCell label="Active facts">
        <Mega>{formatNumeral(stats.active_facts)}</Mega>
      </KpiCell>
      <KpiCell label="Proven rules">
        <Mega>{formatNumeral(stats.proven_rules)}</Mega>
      </KpiCell>
      <KpiCell label="Last write-up">
        {writeup.time == null ? (
          <Mega>—</Mega>
        ) : (
          <span className="flex items-baseline gap-2">
            <span className="font-sans text-[20px] font-medium leading-none tabular-nums text-[var(--fg)]">
              {writeup.time}
            </span>
            <Mono muted>{writeup.factsSub}</Mono>
          </span>
        )}
      </KpiCell>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Pipeline band
// ---------------------------------------------------------------------------

/** A labelled numeral fragment: muted label, --fg tabular numeral. */
function PipeStat({ label, value }: { label: string; value: number }) {
  return (
    <span className="whitespace-nowrap">
      <span className="text-[var(--mfg)]">{label} </span>
      <span className="text-[var(--fg)] tabular-nums">{formatNumeral(value)}</span>
    </span>
  );
}

/** The muted `─→` connector between lifecycle stages. */
function Arrow() {
  return <span className="px-1.5 text-[var(--mfg)]">─→</span>;
}

function PipelineBand({ stats }: { stats: MemoryStats }) {
  const deadLetters = stats.dead_letter_episodes;
  const deadLetterActive = deadLetters > 0;
  return (
    <div className="border-y border-[var(--border-soft)] py-3">
      <div className="flex flex-wrap items-baseline justify-between gap-y-2 font-mono text-[11px] leading-[1.4]">
        <div className="flex flex-wrap items-baseline">
          <PipeStat label="episodes" value={stats.total_episodes} />
          <Arrow />
          <PipeStat label="pending" value={stats.unconsolidated_episodes} />
          <Arrow />
          <span className="whitespace-nowrap">
            <PipeStat label="facts" value={stats.total_facts} />
            <span className="px-1 text-[var(--mfg)]">·</span>
            <PipeStat label="fading" value={stats.fading_facts} />
          </span>
          <Arrow />
          <span className="whitespace-nowrap">
            <PipeStat label="rules" value={stats.total_rules} />
            <span className="px-1 text-[var(--mfg)]">·</span>
            <PipeStat label="proven" value={stats.proven_rules} />
          </span>
        </div>
        {/* dead-letter fragment: the only state color in this band, and only
            when dead_letter > 0 (MEMORY_LANGUAGE.md §6). */}
        <span
          className={cn(
            "whitespace-nowrap tabular-nums",
            deadLetterActive ? "text-[var(--red-text)]" : "text-[var(--mfg)]",
          )}
        >
          dead letters {formatNumeral(deadLetters)}
        </span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// MemoryOverture
// ---------------------------------------------------------------------------

/**
 * The overture (Bands 1 & 2). Reads GET /api/memory/stats and renders the
 * headline, templated Voice sentence, KPI strip, and pipeline band.
 *
 * While stats load, the headline + eyebrow render immediately and the Voice /
 * KPI / pipeline rows reserve their heights so there is no layout shift. The
 * numerals appear in their final value (no count-up animation).
 */
export default function MemoryOverture() {
  const tz = useTimezone();
  const { data: statsResponse, isError, refetch } = useMemoryStats();
  const stats = statsResponse?.data;

  // Degraded fan-out: the backend fans /memory/stats across each butler's pool
  // and drops any that error, naming them in `meta.pools_failed` rather than
  // failing the whole request (memory.py::get_stats). When that list is
  // non-empty the KPI + pipeline totals below undercount, so the confident
  // "everything is remembered" verdict is a half-truth. Name the missing pools
  // inline instead of letting the numbers read as an all-clear (CLAUDE.md
  // degraded-envelope convention; bu-jad4j.1).
  const poolsFailed = statsResponse?.meta?.pools_failed ?? [];

  // Stats down with nothing cached: the Voice/KPI/pipeline bands would
  // otherwise render blank forever, reading as still-loading when the source
  // is actually unreachable. Surface it instead (bu-mkd5r). A background
  // refetch error keeps the last-good `stats` renderable (React Query never
  // clears data on error), so this only trips on a genuine first-load outage.
  const statsUnavailable = isError && stats == null;

  return (
    <section className="flex flex-col gap-8">
      {/* Band 1 — Overture */}
      <div className="flex flex-col gap-4">
        <Eyebrow as="div">Memory</Eyebrow>
        <Display className="max-w-[14ch]">What the house believes.</Display>

        {statsUnavailable ? (
          <MemoryLoadError
            label="memory stats"
            onRetry={() => void refetch()}
            testId="memory-overture-error"
          />
        ) : (
          <>
            {/* Voice sentence — reserve two lines of height to avoid shift. */}
            <div className="min-h-[52px] max-w-[50ch]">
              {stats != null && <Voice>{composeVoiceSentence(stats, tz)}</Voice>}
            </div>

            {/* KPI strip — reserve the strip height while stats load. */}
            <div className="min-h-[72px]">
              {stats != null && <KpiStrip stats={stats} tz={tz} />}
            </div>
          </>
        )}
      </div>

      {/* Band 2 — Pipeline. Reserve the band height (rules + line) so the page
          below does not jump when stats arrive. When one or more butler pools
          dropped out of the fan-out, a named degraded note precedes the band so
          its confident totals are not read as a complete all-clear. */}
      {!statsUnavailable && (
        <div className="flex min-h-[49px] flex-col gap-3">
          {poolsFailed.length > 0 && (
            <SourceDegradedNote
              label="Memory stats"
              detail={`${poolsFailed.join(", ")} unreachable — totals may undercount`}
              onRetry={() => void refetch()}
              testId="memory-overture-pools-degraded"
            />
          )}
          {stats != null && <PipelineBand stats={stats} />}
        </div>
      )}
    </section>
  );
}
