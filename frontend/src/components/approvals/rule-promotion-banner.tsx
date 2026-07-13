/**
 * Rule-Promotion Banner (bu-o62bc, bead 4)
 *
 * Surfaces switchboard rule-promotion suggestions above the approvals metrics:
 *
 * - **Pending cards** — every `route_to:<butler>` suggestion (and any
 *   non-automated skip/metadata_only) invites an explicit owner confirm to mint
 *   a standing ingestion rule, or a dismiss.
 * - **Auto-applied** — the clearly-automated skip/metadata_only tier is applied
 *   automatically (owner gate bu-4pq0s), so it renders informationally (no
 *   confirm button) with the evidence count and a reversible enable/disable of
 *   the minted rule.
 */

import { CheckCircle, ShieldCheck, TrendingUp, X } from "lucide-react";
import { Time } from "@/components/ui/time";
import type { RulePromotionAutoApplied, RulePromotionSuggestion } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";

interface PendingCardProps {
  suggestion: RulePromotionSuggestion;
  onConfirm: (id: string) => void;
  onDismiss: (id: string) => void;
  isPending: boolean;
}

function PendingCard({ suggestion, onConfirm, onDismiss, isPending }: PendingCardProps) {
  const isRouteTo = suggestion.proposed_action.startsWith("route_to:");
  return (
    <Card className="border-blue-200 bg-blue-50/30 dark:border-blue-800 dark:bg-blue-950/20">
      <CardHeader className="pb-2">
        <div className="flex items-start justify-between gap-2">
          <div className="flex items-center gap-2">
            <TrendingUp className="h-4 w-4 text-blue-600 dark:text-blue-400 shrink-0 mt-0.5" />
            <CardTitle className="text-sm font-semibold text-blue-900 dark:text-blue-100">
              Promote to standing rule
            </CardTitle>
          </div>
          <Badge variant="outline" className="text-xs shrink-0">
            {suggestion.evidence_count}× agreed
          </Badge>
        </div>
      </CardHeader>
      <CardContent className="pb-2 space-y-2">
        <p className="text-sm text-foreground/80 font-mono bg-muted/50 rounded px-2 py-1 break-all">
          {suggestion.sender_key} → {suggestion.proposed_action}
        </p>
        <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
          <span>{suggestion.source_channel}</span>
          {isRouteTo && (
            <Badge variant="secondary" className="text-xs px-1 py-0">
              routes traffic
            </Badge>
          )}
          <span>·</span>
          <span>
            Created <Time value={suggestion.created_at} mode="relative" />
          </span>
        </div>
      </CardContent>
      <CardFooter className="pt-0 gap-2">
        <Button
          size="sm"
          variant="default"
          className="bg-blue-600 hover:bg-blue-700 text-white"
          onClick={() => onConfirm(suggestion.id)}
          disabled={isPending}
        >
          <CheckCircle className="h-3.5 w-3.5 mr-1.5" />
          Confirm rule
        </Button>
        <Button size="sm" variant="outline" onClick={() => onDismiss(suggestion.id)} disabled={isPending}>
          <X className="h-3.5 w-3.5 mr-1.5" />
          Dismiss
        </Button>
      </CardFooter>
    </Card>
  );
}

interface AutoAppliedCardProps {
  item: RulePromotionAutoApplied;
  onSetEnabled: (id: string, enabled: boolean) => void;
  isPending: boolean;
}

function AutoAppliedCard({ item, onSetEnabled, isPending }: AutoAppliedCardProps) {
  const enabled = item.rule_enabled !== false;
  return (
    <Card className="border-border bg-muted/30">
      <CardHeader className="pb-2">
        <div className="flex items-start justify-between gap-2">
          <div className="flex items-center gap-2">
            <ShieldCheck className="h-4 w-4 text-muted-foreground shrink-0 mt-0.5" />
            <CardTitle className="text-sm font-semibold text-foreground/80">
              Auto-applied rule
            </CardTitle>
          </div>
          <Badge variant={enabled ? "outline" : "secondary"} className="text-xs shrink-0">
            {enabled ? "active" : "disabled"}
          </Badge>
        </div>
      </CardHeader>
      <CardContent className="pb-2 space-y-2">
        <p className="text-sm text-foreground/80 font-mono bg-muted/50 rounded px-2 py-1 break-all">
          {item.sender_key} → {item.proposed_action}
        </p>
        <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
          <span>{item.source_channel}</span>
          <span>·</span>
          <span>{item.evidence_count}× agreed (clearly automated)</span>
          {item.decided_at && (
            <>
              <span>·</span>
              <span>
                Applied <Time value={item.decided_at} mode="relative" />
              </span>
            </>
          )}
        </div>
      </CardContent>
      <CardFooter className="pt-0 gap-2">
        <Button
          size="sm"
          variant="outline"
          onClick={() => onSetEnabled(item.id, !enabled)}
          disabled={isPending || item.created_rule_id === null}
        >
          {enabled ? "Disable rule" : "Re-enable rule"}
        </Button>
      </CardFooter>
    </Card>
  );
}

interface RulePromotionBannerProps {
  pending: RulePromotionSuggestion[];
  autoApplied: RulePromotionAutoApplied[];
  onConfirm: (id: string) => void;
  onDismiss: (id: string) => void;
  onSetEnabled: (id: string, enabled: boolean) => void;
  /** IDs of suggestions currently being actioned */
  pendingIds?: Set<string>;
}

/**
 * Renders the rule-promotion approvals banner. Returns null when there is
 * nothing pending AND nothing auto-applied to show.
 */
export function RulePromotionBanner({
  pending,
  autoApplied,
  onConfirm,
  onDismiss,
  onSetEnabled,
  pendingIds = new Set(),
}: RulePromotionBannerProps) {
  if (pending.length === 0 && autoApplied.length === 0) return null;

  return (
    <div className="space-y-3" data-testid="rule-promotion-banner">
      {pending.length > 0 && (
        <div className="space-y-3">
          <div className="flex items-center gap-2">
            <h2 className="text-sm font-semibold text-foreground/80">Rule Promotions</h2>
            <Badge variant="secondary" className="text-xs">
              {pending.length}
            </Badge>
          </div>
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {pending.map((s) => (
              <PendingCard
                key={s.id}
                suggestion={s}
                onConfirm={onConfirm}
                onDismiss={onDismiss}
                isPending={pendingIds.has(s.id)}
              />
            ))}
          </div>
        </div>
      )}

      {autoApplied.length > 0 && (
        <div className="space-y-3">
          <div className="flex items-center gap-2">
            <h2 className="text-sm font-semibold text-foreground/80">Auto-applied Rules</h2>
            <Badge variant="secondary" className="text-xs">
              {autoApplied.length}
            </Badge>
          </div>
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {autoApplied.map((item) => (
              <AutoAppliedCard
                key={item.id}
                item={item}
                onSetEnabled={onSetEnabled}
                isPending={pendingIds.has(item.id)}
              />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
