// ---------------------------------------------------------------------------
// EpisodesRegister — the daybook (Band 3, register=episodes) (bu-2ix8d.5)
//
// Episodes render as a journal: day-grouped, reverse-chronological, each day a
// hairline-bound mono header (TODAY / YESTERDAY / dated). Within a day, rows are
// a 4-column grid — mono time gutter, ButlerMark, clamped sans content, and a
// right-edge consolidation glyph.
//
//   ─ TODAY ────────────────────────────────────────────────────────
//   14:21  [c]  Owner mentioned fatigue again during the          ◦
//               afternoon check-in; took ibuprofen.
//
// Ink discipline (MEMORY_LANGUAGE.md §3c, §4, §6):
// - Time gutter is muted by default; it brightens to --fg ONLY when
//   importance >= 8 (importance-as-ink, time only — never the content).
// - ButlerMark is the SOLE carrier of butler hue on the page (outside the
//   activity rail). Content, time, and glyph carry no butler color.
// - The consolidation glyph is the only state color: `✕` (dead_letter/failed)
//   renders --red; `◦` pending and `•` consolidated are colorless. A healthy
//   daybook therefore shows zero red pixels.
//
// Interaction: clicking a row toggles in-place expansion to full content
// (120ms height transition, linear, no fade/scale). The expanded row carries an
// explicit `open ↗` mono link to /memory/episodes/:id as the unambiguous
// navigation affordance (a click on that link navigates; the row body only
// toggles).
//
// Filters: a Pill row over consolidation state writes the `status` URL param —
// `all` (default, param absent) · `pending` · `consolidated` · `dead letter`
// (→ status=dead_letter), mapping to the backend GET /episodes status enum
// filter (bu-awo8k.5).
//
// Binding docs:
// - pr/overview/memory-redesign/prompts/04-register-episodes.md
// - pr/overview/memory-redesign/MEMORY_LANGUAGE.md §3c, §4, §6
// ---------------------------------------------------------------------------

import { useState } from "react";
import { useNavigate } from "react-router";

import { ButlerMark } from "@/components/ui/ButlerMark";
import { Mono } from "@/components/ui/Mono";
import { Pill } from "@/components/ui/Pill";
import { Voice } from "@/components/ui/Voice";
import { useEpisodes } from "@/hooks/use-memory";
import {
  type MemoryEpisodeStatus,
  useMemoryUrlState,
} from "@/hooks/use-memory-url-state";
import {
  consolidationGlyph,
  formatEpisodeTime,
  groupEpisodesByDay,
} from "@/lib/memory-derived";
import { formatNumeral } from "@/lib/memory-overture";
import { cn } from "@/lib/utils";
import type { Episode } from "@/api/types.ts";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** Daybook page size (offset pagination, MEMORY_LANGUAGE.md §3c). */
export const EPISODES_PAGE_SIZE = 50;

/**
 * Importance threshold (inclusive) at which the time gutter brightens to --fg.
 * Below it, the time renders muted. Ink weight, Memory Language §4.
 */
export const IMPORTANCE_INK_THRESHOLD = 8;

/**
 * Status filter pills, in display order. `all` is the default (no `status`
 * param, all statuses); the others map to the API's consolidation status enum.
 * `dead letter` is the human label for the `dead_letter` value.
 */
const STATUS_PILLS: { label: string; value: MemoryEpisodeStatus | "all" }[] = [
  { label: "all", value: "all" },
  { label: "pending", value: "pending" },
  { label: "consolidated", value: "consolidated" },
  { label: "dead letter", value: "dead_letter" },
];

// ---------------------------------------------------------------------------
// Day header
// ---------------------------------------------------------------------------

/** `─ TODAY ─…` — mono 10px uppercase muted label flanked by a hairline rule. */
function DayHeader({ label }: { label: string }) {
  return (
    <div className="flex items-center gap-2 pt-4 pb-1 first:pt-0">
      <span className="h-px w-3 shrink-0 bg-[var(--border-soft)]" aria-hidden />
      <Mono muted className="shrink-0 text-[10px] uppercase tracking-wide">
        {label}
      </Mono>
      <span className="h-px flex-1 bg-[var(--border-soft)]" aria-hidden />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Consolidation glyph
// ---------------------------------------------------------------------------

/**
 * Right-column glyph: `◦` pending · `•` consolidated · `✕` dead_letter/failed.
 * The dead-letter glyph is the ONLY state color in the daybook (--red); the
 * other two are muted/colorless. `title` carries the status word; never a chip,
 * never a tooltip beyond `title`.
 */
function ConsolidationGlyph({ status }: { status: string }) {
  const glyph = consolidationGlyph(status);
  const isDead = status === "dead_letter" || status === "failed";

  return (
    <Mono
      aria-hidden
      title={status}
      className={cn(
        "text-right",
        isDead ? "text-[var(--red)]" : "text-[var(--mfg)]",
      )}
    >
      {glyph}
    </Mono>
  );
}

// ---------------------------------------------------------------------------
// Episode row
// ---------------------------------------------------------------------------

export function EpisodeRow({ episode }: { episode: Episode }) {
  const navigate = useNavigate();
  const [expanded, setExpanded] = useState(false);

  const time = formatEpisodeTime(episode.created_at);
  // Importance is ink: the time gutter — and ONLY the time gutter — brightens to
  // --fg at importance >= 8. Content and glyph never take importance ink.
  const timeBright = episode.importance >= IMPORTANCE_INK_THRESHOLD;

  const openEpisode = () => navigate(`/memory/episodes/${episode.id}`);

  return (
    <div
      className={cn(
        "grid grid-cols-[50px_24px_1fr_16px] items-baseline gap-x-2",
        "border-b border-[var(--border-soft)] px-1 py-2.5",
        "cursor-pointer transition-colors hover:bg-[var(--bg-elev,transparent)]",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--fg)]/20",
        "text-[var(--fg)]",
      )}
      role="button"
      tabIndex={0}
      aria-expanded={expanded}
      onClick={() => setExpanded((e) => !e)}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          setExpanded((v) => !v);
        }
      }}
    >
      {/* Time gutter — mono, muted unless importance >= 8 brightens it. */}
      <Mono muted={!timeBright} className="leading-snug">
        {time}
      </Mono>

      {/* ButlerMark — the sole carrier of butler hue on the page. */}
      <span className="flex items-center">
        <ButlerMark name={episode.butler} tone="neutral" />
      </span>

      {/* Content — sans 13px. Collapsed: clamp 2 lines. Expanded: full content
          revealed via a height transition (no fade, no scale) plus the explicit
          open link. */}
      <div className="min-w-0">
        <div
          className={cn(
            "overflow-hidden transition-[max-height] duration-[120ms] ease-linear",
            expanded ? "max-h-[60rem]" : "max-h-[2.6em]",
          )}
        >
          <span
            className={cn(
              "block text-[13px] leading-snug whitespace-pre-wrap",
              !expanded && "line-clamp-2",
            )}
          >
            {episode.content}
          </span>
          {expanded && (
            <a
              href={`/memory/episodes/${episode.id}`}
              onClick={(e) => {
                // The open link is the unambiguous navigation affordance; the
                // row body only toggles. Stop propagation so a click here does
                // not re-collapse the row before navigating.
                e.preventDefault();
                e.stopPropagation();
                openEpisode();
              }}
              className="mt-1.5 inline-block font-mono text-[11px] text-[var(--mfg)] underline [text-underline-offset:3px] hover:text-[var(--fg)]"
            >
              open ↗
            </a>
          )}
        </div>
      </div>

      {/* Consolidation glyph — right edge. */}
      <ConsolidationGlyph status={episode.consolidation_status} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Status filter pills
// ---------------------------------------------------------------------------

function StatusPills({
  status,
  onSelect,
}: {
  status: MemoryEpisodeStatus | null;
  onSelect: (next: MemoryEpisodeStatus | null) => void;
}) {
  // `all` is represented by a null URL status.
  const active: MemoryEpisodeStatus | "all" = status ?? "all";

  return (
    <div className="flex flex-wrap gap-1.5">
      {STATUS_PILLS.map((pill) => (
        <Pill
          key={pill.value}
          selected={pill.value === active}
          onClick={() => onSelect(pill.value === "all" ? null : pill.value)}
        >
          {pill.label}
        </Pill>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Pagination footer
// ---------------------------------------------------------------------------

/** `1–50 of 1,204` left, prev/next pills right. Mono dim, no numbered pages. */
function PaginationFooter({
  offset,
  count,
  total,
  hasMore,
  onOffset,
}: {
  offset: number;
  count: number;
  total: number;
  hasMore: boolean;
  onOffset: (next: number) => void;
}) {
  if (total === 0) return null;

  const from = offset + 1;
  const to = offset + count;
  const atStart = offset === 0;

  return (
    <div className="flex items-baseline justify-between pt-1">
      <Mono muted>
        {formatNumeral(from)}–{formatNumeral(to)} of {formatNumeral(total)}
      </Mono>
      <div className="flex gap-1.5">
        <Pill
          selected={false}
          disabled={atStart}
          onClick={() => onOffset(Math.max(0, offset - EPISODES_PAGE_SIZE))}
        >
          prev
        </Pill>
        <Pill
          selected={false}
          disabled={!hasMore}
          onClick={() => onOffset(offset + EPISODES_PAGE_SIZE)}
        >
          next
        </Pill>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// EpisodesRegister
// ---------------------------------------------------------------------------

interface EpisodesRegisterProps {
  /** When set, scope all queries to this butler. */
  butlerScope?: string;
  /**
   * Reference instant for TODAY/YESTERDAY day labels. Injectable for
   * deterministic tests; defaults to "now".
   */
  now?: Date;
}

/**
 * The daybook — the episodes register. Reads status + offset from URL state
 * (the merged foundation), fetches episodes filtered to that status, groups
 * them into days client-side, and renders the journal.
 *
 * Pills write `status` to the URL and reset `offset` (the back button restores
 * the previous filter). Pagination writes `offset`. Groups render in the API's
 * order (created_at desc) and a day may split across pages — the header simply
 * repeats at the top of the next page.
 */
export default function EpisodesRegister({ butlerScope, now }: EpisodesRegisterProps) {
  const { state, setState } = useMemoryUrlState();
  const { status, offset } = state;

  const { data: response } = useEpisodes({
    butler: butlerScope,
    status: status ?? undefined,
    offset,
    limit: EPISODES_PAGE_SIZE,
  });

  // useEpisodes() returns PaginatedResponse<Episode> = { data, meta }; unwrap
  // .data before grouping (mirror FactsRegister / RulesRegister unwrap).
  const episodes = response?.data ?? [];
  const total = response?.meta?.total ?? 0;
  const hasMore = response?.meta?.has_more ?? false;

  const groups = groupEpisodesByDay(episodes, now);

  const onSelectStatus = (next: MemoryEpisodeStatus | null) => {
    // Switching filter resets paging to the first page.
    setState({ status: next, offset: 0 });
  };

  return (
    <div className="flex flex-col gap-4">
      <StatusPills status={status} onSelect={onSelectStatus} />

      {episodes.length === 0 ? (
        <Voice variant="italic" className="py-6">
          {status == null ? "Nothing observed yet." : "Nothing in the daybook for this."}
        </Voice>
      ) : (
        <div className="flex flex-col">
          {groups.map((group) => (
            <div key={group.key} className="flex flex-col">
              <DayHeader label={group.label} />
              {group.episodes.map((ep) => (
                <EpisodeRow key={ep.id} episode={ep} />
              ))}
            </div>
          ))}
        </div>
      )}

      <PaginationFooter
        offset={offset}
        count={episodes.length}
        total={total}
        hasMore={hasMore}
        onOffset={(next) => setState({ offset: next })}
      />
    </div>
  );
}
