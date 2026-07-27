/**
 * Rule-Promotion Stats tile (bu-hb61f, bead 6)
 *
 * Closes the loop on measuring the rule-promotion win: how many events promoted
 * rules routed without spawning an LLM session (the headline savings), how many
 * rules are live, the suggestion lifecycle, and the demotion drift signal.
 *
 * Pure/presentational: it takes the already-fetched stats plus the list of
 * degraded source names and renders. Each stat block maps to a backend source
 * (see get_rule_promotion_stats): when a source is degraded the block renders a
 * SourceDegradedNote instead of its numbers, so a failed query never reads as a
 * truthful zero (a broken savings scan must not show "0 sessions avoided").
 */

import { TrendingUp } from "lucide-react";
import type { RulePromotionStats } from "@/api/types";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { SourceDegradedNote } from "@/components/ui/query-boundary";

// Backend source names (get_rule_promotion_stats degraded flags).
const SRC_SUGGESTIONS = "suggestion_counts";
const SRC_PROMOTED_RULES = "promoted_rules";
const SRC_VERDICT = "verdict_metrics";
const PROMOTION_STATS_ICON_CLASS =
  // eslint-disable-next-line no-restricted-syntax -- informational section icon, not live operational status.
  "h-4 w-4 text-blue-600 dark:text-blue-400 shrink-0";

interface StatCellProps {
  eyebrow: string;
  value: number;
  amber?: boolean;
}

function StatCell({ eyebrow, value, amber }: StatCellProps) {
  return (
    <div className="flex flex-col gap-1">
      <span className="text-[10px] font-[var(--font-mono)] uppercase tracking-[0.14em] text-muted-foreground">
        {eyebrow}
      </span>
      <span
        className={
          amber
            ? "text-2xl font-medium tabular-nums text-[var(--amber-text)]"
            : "text-2xl font-medium tabular-nums text-foreground"
        }
      >
        {value.toLocaleString()}
      </span>
    </div>
  );
}

export interface RulePromotionStatsTileProps {
  stats: RulePromotionStats;
  /** Degraded source names from the response meta.sources_degraded. */
  degraded?: string[];
  onRetry?: () => void;
}

export function RulePromotionStatsTile({
  stats,
  degraded = [],
  onRetry,
}: RulePromotionStatsTileProps) {
  const isDegraded = (src: string) => degraded.includes(src);

  return (
    <Card data-testid="rule-promotion-stats">
      <CardHeader className="pb-2">
        <div className="flex items-center gap-2">
          <TrendingUp className={PROMOTION_STATS_ICON_CLASS} />
          <CardTitle className="text-sm font-semibold">Rule promotion</CardTitle>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Savings block (verdict-log derived). */}
        {isDegraded(SRC_VERDICT) ? (
          <SourceDegradedNote
            label="Savings metrics"
            detail="unavailable"
            onRetry={onRetry}
            testId="rule-promotion-stats-verdict-degraded"
          />
        ) : (
          <div className="grid grid-cols-3 gap-4">
            <StatCell
              eyebrow="Sessions avoided (est.)"
              value={stats.llm_sessions_avoided_estimate}
            />
            <StatCell eyebrow="Rule matches" value={stats.promoted_rule_matches} />
            <StatCell eyebrow="Spot-checks" value={stats.promoted_rule_spot_checks} />
          </div>
        )}

        {/* Live promoted rules. */}
        {isDegraded(SRC_PROMOTED_RULES) ? (
          <SourceDegradedNote
            label="Promoted rules"
            detail="unavailable"
            onRetry={onRetry}
            testId="rule-promotion-stats-rules-degraded"
          />
        ) : (
          <div className="grid grid-cols-3 gap-4">
            <StatCell eyebrow="Promoted rules" value={stats.promoted_rules_active} />
          </div>
        )}

        {/* Suggestion lifecycle + drift. */}
        {isDegraded(SRC_SUGGESTIONS) ? (
          <SourceDegradedNote
            label="Suggestion counts"
            detail="unavailable"
            onRetry={onRetry}
            testId="rule-promotion-stats-suggestions-degraded"
          />
        ) : (
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 xl:grid-cols-5">
            <StatCell eyebrow="Pending" value={stats.suggestions_pending} />
            <StatCell eyebrow="Confirmed" value={stats.suggestions_confirmed} />
            <StatCell eyebrow="Dismissed" value={stats.suggestions_dismissed} />
            <StatCell eyebrow="Superseded" value={stats.suggestions_superseded} />
            <StatCell
              eyebrow="Drifting rules"
              value={stats.demotion_pending}
              amber={stats.demotion_pending > 0}
            />
          </div>
        )}

        <p className="text-xs text-muted-foreground">
          Sessions avoided is an estimate: one event routed by a promoted rule is one LLM
          session not spawned, counted since each rule was promoted.
        </p>
      </CardContent>
    </Card>
  );
}
