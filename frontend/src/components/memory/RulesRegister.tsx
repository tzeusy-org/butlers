// ---------------------------------------------------------------------------
// RulesRegister — standing orders (Band 3, register=rules) (bu-2ix8d.4)
//
// Rules render as numbered directives — read, not scanned — with the most
// generous row padding on the page (18px vertical). Each row is a §NN gutter,
// the wrapping directive (clamped to 2 lines in the register), an outcome tally
// line, and a right column carrying the maturity word over the confidence
// numeral. The whole row links to /memory/rules/:id.
//
//   §01  Suggest a sleep study when fatigue is reported          proven
//        three days running.
//        applied 41 · helpful 38 · harmful 1                     0.86
//
// Color discipline (MEMORY_LANGUAGE.md §3b, §6): this register renders state
// color ONLY for harm. The `harmful` word and its numeral take --red exactly
// when harmful_count > 0 (else the whole tally is muted). An anti_pattern rule
// additionally carries a 2px --red left sliver spanning the row — the only
// state color permitted inside a register. No background tint, no icon, no chip.
//
// Maturity is a plain lowercase mono word using the EXACT API vocabulary
// (candidate / established / proven / anti_pattern) — no title-casing, no pill.
//
// Ordering: maturity rank (proven → established → candidate) then confidence
// descending, with anti_pattern rules pinned to the TOP — they demand attention
// and the attention rail links here. §NN numbering is global in render order
// across the filtered list and recomputes on filter change.
//
// Binding docs:
// - pr/overview/memory-redesign/prompts/03-register-rules.md
// - pr/overview/memory-redesign/MEMORY_LANGUAGE.md §3b, §6
// ---------------------------------------------------------------------------

import { useNavigate } from "react-router";

import { Mono } from "@/components/ui/Mono";
import { Pill } from "@/components/ui/Pill";
import { Voice } from "@/components/ui/Voice";
import { useRules } from "@/hooks/use-memory";
import {
  type MemoryMaturity,
  useMemoryUrlState,
} from "@/hooks/use-memory-url-state";
import { formatNumeral } from "@/lib/memory-overture";
import { cn } from "@/lib/utils";
import type { MemoryRule } from "@/api/types.ts";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** Rules register page size (offset pagination, MEMORY_LANGUAGE.md §3b). */
export const RULES_PAGE_SIZE = 50;

/**
 * Maturity filter pills, in display order. `all` is the default (unfiltered)
 * view; the other four are exactly the API's maturity vocabulary. Single-select,
 * writes the `maturity` URL param.
 */
const MATURITY_PILLS: MemoryMaturity[] = [
  "all",
  "candidate",
  "established",
  "proven",
  "anti_pattern",
];

/**
 * Maturity rank for client-side ordering: lower sorts first. anti_pattern is
 * forced to the very top (it demands attention; the rail links here), then
 * proven → established → candidate. Within a rank, confidence sorts descending.
 */
const MATURITY_RANK: Record<string, number> = {
  anti_pattern: 0,
  proven: 1,
  established: 2,
  candidate: 3,
};

// ---------------------------------------------------------------------------
// Ordering
// ---------------------------------------------------------------------------

/**
 * Order rules for the register: anti_pattern pinned to the top, then by
 * maturity rank, then confidence descending. Stable, pure — operates on a copy.
 */
function orderRules(rules: MemoryRule[]): MemoryRule[] {
  return [...rules].sort((a, b) => {
    const rankA = MATURITY_RANK[a.maturity] ?? 99;
    const rankB = MATURITY_RANK[b.maturity] ?? 99;
    if (rankA !== rankB) return rankA - rankB;
    return b.confidence - a.confidence;
  });
}

// ---------------------------------------------------------------------------
// Tally line
// ---------------------------------------------------------------------------

/**
 * `applied N · helpful N · harmful N`, mono 11px muted. When harmful > 0, the
 * `harmful` word and its numeral — ONLY that fragment — take --red. A dataset
 * with zero harm therefore renders zero red pixels here.
 */
function TallyLine({ rule }: { rule: MemoryRule }) {
  const harmful = rule.harmful_count;
  const harmfulRed = harmful > 0;

  return (
    <Mono muted className="whitespace-nowrap">
      applied {formatNumeral(rule.applied_count)}
      <span className="px-1.5">·</span>
      helpful {formatNumeral(rule.success_count)}
      <span className="px-1.5">·</span>
      <span className={cn(harmfulRed && "text-[var(--red)]")}>
        harmful {formatNumeral(harmful)}
      </span>
    </Mono>
  );
}

// ---------------------------------------------------------------------------
// Directive row
// ---------------------------------------------------------------------------

export function DirectiveRow({ rule, index }: { rule: MemoryRule; index: number }) {
  const navigate = useNavigate();

  // §NN — zero-padded, global render-order numbering across the filtered list.
  const ordinal = `§${String(index + 1).padStart(2, "0")}`;

  // anti_pattern is the only in-register state color besides the harmful tally:
  // a 2px --red left sliver spanning the row. No background, no icon.
  const isAntiPattern = rule.maturity === "anti_pattern";

  // The whole row is the hit target → /memory/rules/:id. role=link (not <a>) so
  // the row stays valid HTML; keyboard users get an Enter affordance.
  const openRule = () => navigate(`/memory/rules/${rule.id}`);

  return (
    <div
      role="link"
      tabIndex={0}
      onClick={openRule}
      onKeyDown={(e) => {
        if (e.key === "Enter") openRule();
      }}
      className={cn(
        "grid cursor-pointer grid-cols-[44px_1fr_auto] items-baseline gap-x-3",
        // Generous 18px vertical padding — the most on the page (rules are read).
        "border-b border-[var(--border-soft)] px-1 py-[18px]",
        "transition-colors hover:bg-[var(--bg-elev,transparent)]",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--fg)]/20",
        // 2px --red left sliver for anti_pattern rules; transparent otherwise so
        // the grid geometry never shifts between row kinds.
        "border-l-2",
        isAntiPattern ? "border-l-[var(--red)]" : "border-l-transparent",
        "text-[var(--fg)]",
      )}
    >
      {/* §NN gutter — mono 11px muted, zero-padded */}
      <Mono muted className="leading-snug">
        {ordinal}
      </Mono>

      {/* Directive + tally — sans directive clamped to 2 lines, tally below */}
      <div className="flex min-w-0 flex-col gap-1.5">
        <span className="line-clamp-2 text-[14px] leading-snug">
          {rule.content}
        </span>
        <TallyLine rule={rule} />
      </div>

      {/* Right column — maturity word (lowercase mono, exact API value) over the
          two-decimal confidence numeral, tabular. */}
      <div className="flex flex-col items-end gap-1.5 whitespace-nowrap text-right">
        <Mono muted>{rule.maturity}</Mono>
        <Mono>{rule.confidence.toFixed(2)}</Mono>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Filter pills
// ---------------------------------------------------------------------------

function MaturityPills({
  maturity,
  onSelect,
}: {
  maturity: MemoryMaturity;
  onSelect: (m: MemoryMaturity) => void;
}) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {MATURITY_PILLS.map((m) => (
        <Pill key={m} selected={m === maturity} onClick={() => onSelect(m)}>
          {m}
        </Pill>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Pagination footer
// ---------------------------------------------------------------------------

/** `1–50 of 312` left, prev/next pills right. Mono dim, no numbered pages. */
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
          onClick={() => onOffset(Math.max(0, offset - RULES_PAGE_SIZE))}
        >
          prev
        </Pill>
        <Pill
          selected={false}
          disabled={!hasMore}
          onClick={() => onOffset(offset + RULES_PAGE_SIZE)}
        >
          next
        </Pill>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// RulesRegister
// ---------------------------------------------------------------------------

interface RulesRegisterProps {
  /** When set, scope all queries to this butler. */
  butlerScope?: string;
}

/**
 * The standing orders — the rules register. Reads maturity + offset from URL
 * state (the merged foundation), fetches rules filtered to that maturity, and
 * renders numbered directive rows ordered with anti_pattern pinned to the top.
 *
 * Pills write `maturity` to the URL and reset `offset` (the back button restores
 * the previous filter). Pagination writes `offset`. §NN numbering recomputes on
 * filter change with no layout shift (the sliver column is always 2px).
 */
export default function RulesRegister({ butlerScope }: RulesRegisterProps) {
  const { state, setState } = useMemoryUrlState();
  const { maturity, offset } = state;

  // `all` is the unfiltered view; the API takes a concrete maturity string.
  const maturityFilter = maturity === "all" ? undefined : maturity;

  const { data: response } = useRules({
    maturity: maturityFilter,
    scope: butlerScope,
    offset,
    limit: RULES_PAGE_SIZE,
  });

  // useRules() returns PaginatedResponse<MemoryRule> = { data, meta }; unwrap
  // .data before mapping (mirror FactsRegister / MemoryOverture unwrap).
  const rules = response?.data ?? [];
  const total = response?.meta?.total ?? 0;
  const hasMore = response?.meta?.has_more ?? false;

  // anti_pattern pinned top, then maturity rank, then confidence desc.
  const ordered = orderRules(rules);

  const onSelectMaturity = (m: MemoryMaturity) => {
    // Switching filter resets paging to the first page.
    setState({ maturity: m, offset: 0 });
  };

  return (
    <div className="flex flex-col gap-4">
      <MaturityPills maturity={maturity} onSelect={onSelectMaturity} />

      {ordered.length === 0 ? (
        <Voice variant="italic" className="py-6">
          {maturity === "all"
            ? "No standing orders yet."
            : "Nothing of this maturity."}
        </Voice>
      ) : (
        <div className="flex flex-col">
          {ordered.map((rule, i) => (
            <DirectiveRow key={rule.id} rule={rule} index={i} />
          ))}
        </div>
      )}

      <PaginationFooter
        offset={offset}
        count={ordered.length}
        total={total}
        hasMore={hasMore}
        onOffset={(next) => setState({ offset: next })}
      />
    </div>
  );
}
