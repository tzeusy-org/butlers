// ---------------------------------------------------------------------------
// SearchResults — results mode for the register area (bu-2ix8d.6)
//
// While a query is active (`q` URL param set), the register area renders this
// instead of the browse register. Results come from GET /api/memory/inspect
// (kind-scoped), grouped under mono kind-headers (`FACTS · 12`, `RULES · 2`,
// `EPISODES · 7`); each group's rows render in that kind's register shape by
// REUSING the existing register row components (LedgerRow / DirectiveRow /
// EpisodeRow). No fourth shape — a fact row here is byte-identical to a fact
// row in browse mode.
//
// Each MemoryInspectResult carries a full register-shaped row (fact/rule/
// episode, matching its kind — #2199); we adapt each into its domain type
// (memory-derived inspectResultTo*), which prefers that embedded row so rows
// render real belief / maturity / importance data identical to browse mode
// (honest-default fallback only for rows that predate the embedded row).
//
// Empty result: one serif-italic line — "Nothing in the books." (§3d) — only
// after a successful, healthy, empty response. While the query is in flight a
// loading line stands in; when a pool dropped out (`meta.pools_failed`) the
// degraded note names it, with or without rows (bu-kqnum.14.5).
//
// Paging: `offset` lives in the URL (owned by MemoryBrowser); the footer reads
// the exact `meta.total` ("showing 1–50 of 312") and moves only the offset.
//
// Binding docs:
// - (memory house-ledger redesign, graduated) prompts/05-search-and-rail.md Part 1
// - (memory house-ledger redesign, graduated) MEMORY_LANGUAGE.md §3d
// ---------------------------------------------------------------------------

import { Eyebrow } from "@/components/ui/Eyebrow";
import { Mono } from "@/components/ui/Mono";
import { Pill } from "@/components/ui/Pill";
import { Voice } from "@/components/ui/Voice";
import { MemoryLoadError } from "@/components/memory/MemoryLoadError";
import { PoolsDegradedNote } from "@/components/memory/PoolsDegradedNote";
import { LedgerRow } from "@/components/memory/FactsRegister";
import { DirectiveRow } from "@/components/memory/RulesRegister";
import { EpisodeRow } from "@/components/memory/EpisodesRegister";
import { useMemoryInspect } from "@/hooks/use-memory";
import {
  inspectResultToEpisode,
  inspectResultToFact,
  inspectResultToRule,
} from "@/lib/memory-derived";
import { formatNumeral } from "@/lib/memory-overture";
import type { MemoryInspectResult } from "@/api/types.ts";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** Inspect page size — one fetch covers all kinds (fanned out server-side). */
const SEARCH_LIMIT = 50;

/** Render order of kind groups. */
const KIND_ORDER = ["fact", "rule", "episode"] as const;

/** Mono group-header label for a kind (uppercase plural product vocabulary). */
const KIND_LABEL: Record<string, string> = {
  fact: "FACTS",
  rule: "RULES",
  episode: "EPISODES",
};

// ---------------------------------------------------------------------------
// Group header
// ---------------------------------------------------------------------------

/** `showing 1–50 of 312` left, prev/next pills right (mono dim, no numbered pages). */
function PaginationFooter({
  offset,
  count,
  total,
  hasMore,
  partial,
  onOffset,
}: {
  offset: number;
  count: number;
  total: number;
  hasMore: boolean;
  /** True when a pool dropped out, so the total undercounts. */
  partial: boolean;
  onOffset: (next: number) => void;
}) {
  if (total === 0) return null;

  const from = count === 0 ? offset : offset + 1;
  const to = offset + count;

  return (
    <div className="flex items-baseline justify-between pt-1" data-testid="memory-search-pager">
      <Mono muted>
        showing {formatNumeral(from)}–{formatNumeral(to)} of {formatNumeral(total)}
        {partial ? " (partial)" : ""}
      </Mono>
      <div className="flex gap-1.5">
        <Pill
          selected={false}
          disabled={offset === 0}
          onClick={() => onOffset(Math.max(0, offset - SEARCH_LIMIT))}
        >
          prev
        </Pill>
        <Pill selected={false} disabled={!hasMore} onClick={() => onOffset(offset + SEARCH_LIMIT)}>
          next
        </Pill>
      </div>
    </div>
  );
}

/** `FACTS · 12` — mono kind eyebrow with a hairline rule beneath it. */
function GroupHeader({ kind, count }: { kind: string; count: number }) {
  return (
    <div className="border-b border-[var(--border-soft)] pb-1.5">
      <Eyebrow as="div">
        {KIND_LABEL[kind] ?? kind.toUpperCase()} · {formatNumeral(count)}
      </Eyebrow>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Per-kind group — reuses the register row components verbatim
// ---------------------------------------------------------------------------

function FactGroup({ results, now }: { results: MemoryInspectResult[]; now?: Date }) {
  return (
    <div className="flex flex-col gap-2">
      <GroupHeader kind="fact" count={results.length} />
      <div className="flex flex-col">
        {results.map((r) => (
          <LedgerRow key={r.id} fact={inspectResultToFact(r)} now={now} />
        ))}
      </div>
    </div>
  );
}

function RuleGroup({ results }: { results: MemoryInspectResult[] }) {
  return (
    <div className="flex flex-col gap-2">
      <GroupHeader kind="rule" count={results.length} />
      <div className="flex flex-col">
        {results.map((r, i) => (
          <DirectiveRow key={r.id} rule={inspectResultToRule(r)} index={i} />
        ))}
      </div>
    </div>
  );
}

function EpisodeGroup({ results }: { results: MemoryInspectResult[] }) {
  return (
    <div className="flex flex-col gap-2">
      <GroupHeader kind="episode" count={results.length} />
      <div className="flex flex-col">
        {results.map((r) => (
          <EpisodeRow key={r.id} episode={inspectResultToEpisode(r)} />
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// SearchResults
// ---------------------------------------------------------------------------

interface SearchResultsProps {
  /** Active query (already known to be non-null by the caller). */
  q: string;
  /** Search scope: 'all' (param absent) or a singular inspect kind. */
  kind: "all" | "fact" | "rule" | "episode";
  /** Reference instant for the ledger belief numeral / daybook (tests). */
  now?: Date;
  /** Page offset (from the URL). Defaults to the first page. */
  offset?: number;
  /** Move to another page; writes only the offset. */
  onOffset?: (next: number) => void;
}

/**
 * Results mode for the register area. Fetches inspect results for the active
 * query + kind scope, groups them by kind, and renders each group's rows in its
 * register shape. Empty result is one serif-italic line.
 */
export default function SearchResults({ q, kind, now, offset = 0, onOffset }: SearchResultsProps) {
  const { data: response, isError, isLoading, refetch } = useMemoryInspect({
    q,
    kind: kind === "all" ? undefined : kind,
    offset,
    limit: SEARCH_LIMIT,
  });
  const total = response?.meta?.total ?? 0;
  const hasMore = response?.meta?.has_more ?? false;
  const poolsFailed = response?.meta?.pools_failed ?? [];

  // useMemoryInspect returns PaginatedResponse<MemoryInspectResult> = {data,meta}
  // — unwrap .data before grouping (the #2190/#2191 envelope pattern).
  const results = response?.data ?? [];

  // Group by kind, preserving each kind's incoming (created_at desc) order.
  const byKind: Record<string, MemoryInspectResult[]> = {
    fact: [],
    rule: [],
    episode: [],
  };
  for (const r of results) {
    (byKind[r.kind] ??= []).push(r);
  }

  if (isError && results.length === 0) {
    // A failed search must not render "Nothing in the books." — a down inspect
    // endpoint would read as a genuine no-match result (bu-mkd5r).
    return (
      <MemoryLoadError
        label="search results"
        onRetry={() => void refetch()}
        testId="memory-search-error"
      />
    );
  }

  if (isLoading) {
    // An in-flight search has no verdict yet; never render the empty line.
    return (
      <div role="status" data-testid="memory-search-loading">
        <Voice variant="italic" className="py-6">
          Searching the books…
        </Voice>
      </div>
    );
  }

  const degradedNote = (
    <PoolsDegradedNote
      label="Search results"
      poolsFailed={poolsFailed}
      onRetry={() => void refetch()}
      testId="memory-search-degraded"
    />
  );
  const footer = (
    <PaginationFooter
      offset={offset}
      count={results.length}
      total={total}
      hasMore={hasMore}
      partial={poolsFailed.length > 0}
      onOffset={(next) => onOffset?.(next)}
    />
  );

  if (results.length === 0) {
    if (poolsFailed.length > 0) {
      // A pool dropped out: an empty page is not a "no match" verdict.
      return <div className="flex flex-col gap-4">{degradedNote}{footer}</div>;
    }
    if (total > 0) {
      // Paged past the last result: say so and keep the way back.
      return (
        <div className="flex flex-col gap-4">
          <Voice variant="italic" className="py-6">
            Nothing on this page.
          </Voice>
          {footer}
        </div>
      );
    }
    return (
      <Voice variant="italic" className="py-6">
        Nothing in the books.
      </Voice>
    );
  }

  return (
    <div className="flex flex-col gap-8">
      {degradedNote}
      {KIND_ORDER.map((k) => {
        const group = byKind[k];
        if (!group || group.length === 0) return null;
        if (k === "fact") return <FactGroup key={k} results={group} now={now} />;
        if (k === "rule") return <RuleGroup key={k} results={group} />;
        return <EpisodeGroup key={k} results={group} />;
      })}
      {footer}
    </div>
  );
}
