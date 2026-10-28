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
import { Voice } from "@/components/ui/Voice";
import { QueryBoundary } from "@/components/ui/query-boundary";
import { MemoryPaginationFooter } from "@/components/memory/MemoryPaginationFooter";
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
import type { MemoryInspectResult, MemoryPaginatedResponse } from "@/api/types.ts";

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
  const { data: response, isError, isPending, refetch } = useMemoryInspect({
    q,
    kind: kind === "all" ? undefined : kind,
    offset,
    limit: SEARCH_LIMIT,
  });

  return (
    <QueryBoundary
      // A paused first read is pending without fetching. It has no empty verdict.
      isLoading={isPending || (!response && !isError)}
      isError={isError}
      isEmpty={false}
      sourceLabel="search results"
      onRetry={() => void refetch()}
      loadingFallback={
        <div role="status" data-testid="memory-search-loading">
          <Voice variant="italic" className="py-6">
            Searching the books…
          </Voice>
        </div>
      }
      emptyFallback={null}
    >
      {response && (
        <SearchResultRows
          response={response}
          now={now}
          offset={offset}
          onOffset={onOffset}
          onRetry={() => void refetch()}
        />
      )}
    </QueryBoundary>
  );
}

/** Only the boundary's success branch renders rows or an empty verdict. */
function SearchResultRows({
  response, now, offset, onOffset, onRetry,
}: Pick<SearchResultsProps, "now" | "onOffset"> & {
  response: MemoryPaginatedResponse<MemoryInspectResult>;
  offset: number;
  onRetry: () => void;
}) {
  const { total, has_more: hasMore, pools_failed: poolsFailedMeta } = response.meta;
  const poolsFailed = poolsFailedMeta ?? [];

  // useMemoryInspect returns PaginatedResponse<MemoryInspectResult> = {data,meta}
  // — unwrap .data before grouping (the #2190/#2191 envelope pattern).
  const results = response.data;

  // Group by kind, preserving each kind's incoming (created_at desc) order.
  const byKind: Record<string, MemoryInspectResult[]> = {
    fact: [],
    rule: [],
    episode: [],
  };
  for (const r of results) {
    (byKind[r.kind] ??= []).push(r);
  }

  const degradedNote = (
    <PoolsDegradedNote
      label="Search results"
      poolsFailed={poolsFailed}
      onRetry={onRetry}
      testId="memory-search-degraded"
    />
  );
  const footer = (
    <MemoryPaginationFooter
      offset={offset}
      count={results.length}
      total={total}
      hasMore={hasMore}
      pageSize={SEARCH_LIMIT}
      partial={poolsFailed.length > 0}
      onOffset={(next) => onOffset?.(next)}
      prefix="showing "
      testId="memory-search-pager"
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
