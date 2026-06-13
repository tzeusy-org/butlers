// ---------------------------------------------------------------------------
// FactsRegister — the ledger (Band 3, register=facts, the default) (bu-2ix8d.3)
//
// Facts render as ledger rows: a subject·predicate / content / belief grid with
// a hairline between rows, the whole row a link to /memory/facts/:id.
//
//   subject · predicate              content                      belief
//   ─────────────────────────────────────────────────────────────────────
//   Owner · preferred_pain_relief    ibuprofen, after meals       0.94 st
//   Wei · favorite_coffee            flat white, oat milk         0.31 vo ↳  ← dimmed
//
// Confidence is ink (MEMORY_LANGUAGE.md §3a, §4): a fact the server reports as
// `validity === 'fading'` dims its WHOLE row — all three cells including the
// entity link — to `--dim`. No color, no italic, no opacity transition, no
// badge. Dimming is the single affordance for decay. The belief NUMERAL is the
// decayed effectiveConfidence() value; dimming is driven by server validity
// (the server owns the threshold — see memory-derived.ts).
//
// Color discipline (§6): this register renders ZERO red/amber/green pixels under
// any data. State color lives only in the attention rail.
//
// Binding docs:
// - pr/overview/memory-redesign/prompts/02-register-facts.md
// - pr/overview/memory-redesign/MEMORY_LANGUAGE.md §3a, §4, §6
// ---------------------------------------------------------------------------

import { Link, useNavigate } from "react-router";

import { Mono } from "@/components/ui/Mono";
import { Pill } from "@/components/ui/Pill";
import { Voice } from "@/components/ui/Voice";
import { useFacts } from "@/hooks/use-memory";
import {
  type MemoryValidity,
  useMemoryUrlState,
} from "@/hooks/use-memory-url-state";
import { effectiveConfidence, permanenceTag } from "@/lib/memory-derived";
import { formatNumeral } from "@/lib/memory-overture";
import { cn } from "@/lib/utils";
import type { Fact } from "@/api/types.ts";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** Ledger page size (offset pagination, MEMORY_LANGUAGE.md §3a). */
export const FACTS_PAGE_SIZE = 50;

/**
 * Validity filter pills, in display order. `active` is the default register
 * view; non-active validities NEVER render unless their pill is selected
 * (§3a: "non-active validities never render unfiltered").
 */
const VALIDITY_PILLS: MemoryValidity[] = [
  "active",
  "fading",
  "superseded",
  "expired",
  "retracted",
];

// ---------------------------------------------------------------------------
// Belief numeral
// ---------------------------------------------------------------------------

/** Two-decimal effective confidence, e.g. `0.94`. No `%` (§4). */
function formatConfidence(fact: Fact, now?: Date): string {
  return effectiveConfidence(fact, now).toFixed(2);
}

// ---------------------------------------------------------------------------
// Ledger row
// ---------------------------------------------------------------------------

export function LedgerRow({ fact, now }: { fact: Fact; now?: Date }) {
  const navigate = useNavigate();

  // Confidence is ink: a fading fact dims the WHOLE row to --dim. Active facts
  // (and every other filtered validity) render at full --fg foreground. The
  // server owns the fading threshold; we read fact.validity, not the numeral.
  const dimmed = fact.validity === "fading";
  const rowColor = dimmed ? "text-[var(--dim)]" : "text-[var(--fg)]";

  const confidence = formatConfidence(fact, now);
  const tag = permanenceTag(fact.permanence);
  const hasProvenance = fact.source_episode_id != null;

  // The whole row is the hit target → /memory/facts/:id. It is NOT an <a> so the
  // entity-anchor <Link> inside it stays valid HTML (no nested anchors). Keyboard
  // users get an Enter affordance via role/tabIndex.
  const openFact = () => navigate(`/memory/facts/${fact.id}`);

  return (
    <div
      role="link"
      tabIndex={0}
      onClick={openFact}
      onKeyDown={(e) => {
        if (e.key === "Enter") openFact();
      }}
      className={cn(
        "grid cursor-pointer grid-cols-[minmax(180px,0.8fr)_1fr_auto] items-baseline gap-x-4",
        "border-b border-[var(--border-soft)] px-1 py-2.5",
        "transition-colors hover:bg-[var(--bg-elev,transparent)]",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--fg)]/20",
        rowColor,
      )}
    >
      {/* subject · predicate — sans subject, mono muted predicate */}
      <span className="min-w-0 truncate text-[13px] leading-snug">
        {fact.entity_id != null ? (
          <Link
            to={`/entities/${fact.entity_id}`}
            // Entity-anchored subjects link out; clicking the anchor must not
            // open the fact (stop propagation), and it inherits the row color
            // so a dimmed row dims its anchor too.
            onClick={(e) => e.stopPropagation()}
            className="underline [text-underline-offset:4px] hover:text-[var(--fg)]"
          >
            {fact.entity_name ?? fact.subject}
          </Link>
        ) : (
          <span>{fact.subject}</span>
        )}
        <span className="px-1.5 font-mono text-[11px] text-[var(--mfg)]">·</span>
        <span className="font-mono text-[11px] text-[var(--mfg)]">
          {fact.predicate}
        </span>
      </span>

      {/* content — single line, truncated */}
      <span className="min-w-0 truncate text-[13px] leading-snug">
        {fact.content}
      </span>

      {/* belief — mono tabular, right-aligned: confidence · permanence · ↳ */}
      <span className="flex items-baseline justify-end gap-1.5 whitespace-nowrap font-mono text-[11px] tabular-nums">
        <span>{confidence}</span>
        <span className="text-[var(--mfg)]">{tag}</span>
        {hasProvenance && (
          <span
            // Provenance glyph: muted, hover reveals the source episode. Not a
            // separate link in the register — provenance navigation lives on
            // the detail page (§3a).
            aria-hidden
            title={`from episode ${(fact.source_episode_id ?? "").slice(0, 8)}`}
            className="text-[var(--mfg)]"
          >
            ↳
          </span>
        )}
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Filter pills
// ---------------------------------------------------------------------------

function ValidityPills({
  validity,
  onSelect,
}: {
  validity: MemoryValidity;
  onSelect: (v: MemoryValidity) => void;
}) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {VALIDITY_PILLS.map((v) => (
        <Pill key={v} selected={v === validity} onClick={() => onSelect(v)}>
          {v}
        </Pill>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Pagination footer
// ---------------------------------------------------------------------------

/** `1–50 of 3,182` left, prev/next pills right. Mono dim, no numbered pages. */
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
          onClick={() => onOffset(Math.max(0, offset - FACTS_PAGE_SIZE))}
        >
          prev
        </Pill>
        <Pill
          selected={false}
          disabled={!hasMore}
          onClick={() => onOffset(offset + FACTS_PAGE_SIZE)}
        >
          next
        </Pill>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// FactsRegister
// ---------------------------------------------------------------------------

interface FactsRegisterProps {
  /** When set, scope all queries to this butler. */
  butlerScope?: string;
  /**
   * Reference instant for the belief numeral (effectiveConfidence). Injectable
   * for deterministic tests; defaults to "now" inside effectiveConfidence.
   */
  now?: Date;
}

/**
 * The ledger — the default facts register. Reads validity + offset from URL
 * state (the merged foundation), fetches facts filtered to that validity, and
 * renders ledger rows with confidence-as-ink dimming.
 *
 * Pills write `validity` to the URL and reset `offset` (the back button
 * restores the previous filter). Pagination writes `offset`. No skeleton pulse:
 * the row area reserves no fixed height, rows render as they arrive (§8).
 */
export default function FactsRegister({ butlerScope, now }: FactsRegisterProps) {
  const { state, setState } = useMemoryUrlState();
  const { validity, offset } = state;

  const { data: response } = useFacts({
    validity,
    scope: butlerScope,
    offset,
    limit: FACTS_PAGE_SIZE,
  });

  // useFacts() returns PaginatedResponse<Fact> = { data: Fact[], meta }; unwrap
  // .data before mapping (mirror the MemoryOverture unwrap).
  const facts = response?.data ?? [];
  const total = response?.meta?.total ?? 0;
  const hasMore = response?.meta?.has_more ?? false;

  const onSelectValidity = (v: MemoryValidity) => {
    // Switching filter resets paging to the first page.
    setState({ validity: v, offset: 0 });
  };

  return (
    <div className="flex flex-col gap-4">
      <ValidityPills validity={validity} onSelect={onSelectValidity} />

      {facts.length === 0 ? (
        <Voice variant="italic" className="py-6">
          {validity === "active"
            ? "The ledger is empty."
            : "No facts answer this."}
        </Voice>
      ) : (
        <div className="flex flex-col">
          {facts.map((fact) => (
            <LedgerRow key={fact.id} fact={fact} now={now} />
          ))}
        </div>
      )}

      <PaginationFooter
        offset={offset}
        count={facts.length}
        total={total}
        hasMore={hasMore}
        onOffset={(next) => setState({ offset: next })}
      />
    </div>
  );
}
