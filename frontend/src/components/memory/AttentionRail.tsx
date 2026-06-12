// ---------------------------------------------------------------------------
// AttentionRail — Band 3 right column of the /memory house-ledger (bu-2ix8d.6)
//
// The rail is the ONLY surface on the page where state demands color (the one
// exception is the pipeline band's dead-letter numeral). Two stacked sections
// under mono eyebrows:
//
//   NEEDS ATTENTION — up to five condition rows, each rendering only when its
//   condition holds (24px glyph · title + serif detail · underlined-word action
//   with `→`, never a button). When none hold, the eyebrow stays and the body
//   is one serif-italic line: "Nothing waiting." A fully healthy page therefore
//   shows the eyebrow + that line and zero red/amber pixels.
//
//   RECENT ACTIVITY — a de-carded quiet list: mono time · neutral ButlerMark ·
//   sans summary, truncated. No color, no severity, no links. Auto-refreshes
//   every 15s (the existing hook interval).
//
// The five attention conditions (prompt 05 Part 2):
//   1. dead-letter episodes > 0           red    → episodes register, dead letter
//   2. write-up overdue (>2× cadence)     amber  ACTION-LESS (informational)
//   3. anti-pattern rules > 0             red    → rules register, anti_pattern
//   4. important facts fading (≥8)        amber  → ledger filtered to fading
//   5. stale embeddings > 0               amber  → housekeeping anchor
//
// Binding docs:
// - pr/overview/memory-redesign/prompts/05-search-and-rail.md Part 2
// - pr/overview/memory-redesign/MEMORY_LANGUAGE.md §5, §6, §7
// ---------------------------------------------------------------------------

import { Link } from "react-router";

import { ButlerMark } from "@/components/ui/ButlerMark";
import { Eyebrow } from "@/components/ui/Eyebrow";
import { Mono } from "@/components/ui/Mono";
import { Voice } from "@/components/ui/Voice";
import {
  useFacts,
  useMemoryActivity,
  useMemoryStats,
} from "@/hooks/use-memory";
import { useReembedPending } from "@/hooks/use-memory-reembed";
import {
  IMPORTANT_FACT_THRESHOLD,
  formatEpisodeTime,
  isWriteupOverdue,
} from "@/lib/memory-derived";
import { formatNumeral } from "@/lib/memory-overture";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Severity
// ---------------------------------------------------------------------------

type Severity = "red" | "amber";

/** A resolved attention row (only built when its condition holds). */
interface AttentionItem {
  key: string;
  severity: Severity;
  /** Sans title line (13px). */
  title: string;
  /** Serif detail line (13px, Voice small variant). */
  detail: string;
  /** Action: underlined word + `→` linking to a pre-filtered view. */
  action?: { label: string; to: string };
}

function severityColor(severity: Severity): string {
  return severity === "red" ? "text-[var(--red)]" : "text-[var(--amber)]";
}

// ---------------------------------------------------------------------------
// Attention row
// ---------------------------------------------------------------------------

/**
 * One attention row: a 6px severity-colored square glyph, a title + serif
 * detail, and an optional underlined action word with `→` (never a button).
 */
function AttentionRow({ item }: { item: AttentionItem }) {
  return (
    <div className="grid grid-cols-[24px_1fr_auto] items-baseline gap-x-2 py-[18px]">
      {/* 6px square glyph in severity color, baseline-aligned with the title. */}
      <span className="flex h-[13px] items-center">
        <span
          aria-hidden
          className={cn(
            "inline-block size-[6px]",
            item.severity === "red" ? "bg-[var(--red)]" : "bg-[var(--amber)]",
          )}
        />
      </span>

      <div className="flex min-w-0 flex-col gap-1">
        <span className="text-[13px] leading-snug text-[var(--fg)]">
          {item.title}
        </span>
        <Voice as="span" className="text-[13px] leading-snug text-[var(--mfg)]">
          {item.detail}
        </Voice>
      </div>

      {item.action ? (
        <Link
          to={item.action.to}
          className={cn(
            "whitespace-nowrap text-[13px] underline [text-underline-offset:4px]",
            severityColor(item.severity),
            "hover:text-[var(--fg)]",
          )}
        >
          {item.action.label} →
        </Link>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Recent activity (de-carded)
// ---------------------------------------------------------------------------

/** Quiet activity row: mono time · neutral ButlerMark · sans summary. */
function ActivityRow({
  time,
  butler,
  summary,
}: {
  time: string;
  butler: string | null;
  summary: string;
}) {
  return (
    <div className="grid grid-cols-[44px_24px_1fr] items-baseline gap-x-2 py-2.5">
      <Mono muted className="leading-snug">
        {time}
      </Mono>
      <span className="flex items-center">
        <ButlerMark name={butler ?? "general"} tone="neutral" />
      </span>
      <span className="min-w-0 truncate text-[13px] leading-snug text-[var(--fg)]">
        {summary}
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// AttentionRail
// ---------------------------------------------------------------------------

interface AttentionRailProps {
  /** Reference instant for the write-up-overdue check / activity times. */
  now?: Date;
}

/**
 * The attention rail. Derives the (up to five) attention conditions from stats,
 * a fading-important-facts count, and the stale-embedding count, then renders
 * the NEEDS ATTENTION list (or "Nothing waiting.") above the de-carded RECENT
 * ACTIVITY list. State color appears ONLY here.
 */
export default function AttentionRail({ now }: AttentionRailProps) {
  const { data: statsResp } = useMemoryStats();
  const stats = statsResp?.data;

  // Important fading facts: validity=fading ∧ importance >= 8. We only need the
  // count, so request a single row and read meta.total (the #2185 importance_min
  // filter, exposed on FactParams).
  const { data: fadingResp } = useFacts({
    validity: "fading",
    importance_min: IMPORTANT_FACT_THRESHOLD,
    limit: 1,
  });
  const importantFadingCount = fadingResp?.meta?.total ?? 0;

  const { data: reembedResp } = useReembedPending();
  const staleEmbeddings = reembedResp?.data?.total ?? 0;

  const { data: activityResp } = useMemoryActivity(20);
  const activity = activityResp?.data ?? [];

  // Build the attention list in severity/priority order; each row appears only
  // when its condition holds.
  const items: AttentionItem[] = [];

  if (stats != null) {
    // 1. dead-letter episodes (red)
    if (stats.dead_letter_episodes > 0) {
      const n = stats.dead_letter_episodes;
      items.push({
        key: "dead-letters",
        severity: "red",
        title: `${formatNumeral(n)} ${n === 1 ? "episode" : "episodes"} dead-lettered`,
        detail: "Consolidation gave up after repeated failures.",
        action: {
          label: "review",
          to: "/memory?register=episodes&status=dead_letter",
        },
      });
    }

    // 2. write-up overdue (amber, ACTION-LESS / informational)
    if (isWriteupOverdue(stats.last_consolidation_at, now)) {
      items.push({
        key: "writeup-overdue",
        severity: "amber",
        title: "Write-up overdue",
        detail: "The evening write-up has not run on schedule.",
        // No action — informational only (prompt 05: "none (informational)").
      });
    }

    // 3. anti-pattern rules (red)
    if (stats.anti_pattern_rules > 0) {
      const n = stats.anti_pattern_rules;
      items.push({
        key: "anti-pattern-rules",
        severity: "red",
        title: `${formatNumeral(n)} anti-pattern ${n === 1 ? "rule" : "rules"}`,
        detail: "Standing orders found to do more harm than good.",
        action: {
          label: "review",
          to: "/memory?register=rules&maturity=anti_pattern",
        },
      });
    }
  }

  // 4. important facts fading (amber)
  if (importantFadingCount > 0) {
    items.push({
      key: "important-fading",
      severity: "amber",
      title: `${formatNumeral(importantFadingCount)} important ${
        importantFadingCount === 1 ? "fact" : "facts"
      } fading`,
      detail: "High-importance entries are losing confidence unconfirmed.",
      action: { label: "review", to: "/memory?register=facts&validity=fading" },
    });
  }

  // 5. stale embeddings (amber)
  if (staleEmbeddings > 0) {
    items.push({
      key: "stale-embeddings",
      severity: "amber",
      title: `${formatNumeral(staleEmbeddings)} rows on an old embedding`,
      detail: "Rows still carry vectors from a superseded model.",
      action: { label: "housekeeping", to: "/memory#housekeeping" },
    });
  }

  return (
    <div className="flex flex-col gap-10">
      {/* NEEDS ATTENTION */}
      <div className="flex flex-col gap-1">
        <Eyebrow as="div">Needs attention</Eyebrow>
        {items.length === 0 ? (
          <Voice variant="italic" className="py-4">
            Nothing waiting.
          </Voice>
        ) : (
          <div className="flex flex-col divide-y divide-[var(--border-soft)]">
            {items.map((item) => (
              <AttentionRow key={item.key} item={item} />
            ))}
          </div>
        )}
      </div>

      {/* RECENT ACTIVITY (de-carded) */}
      <div className="flex flex-col gap-2">
        <Eyebrow as="div">Recent activity</Eyebrow>
        {activity.length === 0 ? (
          <Voice variant="italic" className="py-4">
            Nothing observed yet.
          </Voice>
        ) : (
          <div className="flex flex-col">
            {activity.map((item) => (
              <ActivityRow
                key={`${item.type}-${item.id}`}
                time={formatEpisodeTime(item.created_at)}
                butler={item.butler}
                summary={item.summary}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
