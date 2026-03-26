/**
 * Autonomy Suggestions Banner
 *
 * Renders promotion and demotion suggestion cards in a banner above the
 * approvals metrics. Promotion cards invite the user to create a standing rule
 * for a frequently-approved pattern; demotion cards warn about a failing
 * auto-approved pattern and invite the user to revoke the standing rule.
 */

import { formatDistanceToNow } from "date-fns";
import { AlertTriangle, CheckCircle, Clock, TrendingUp, X } from "lucide-react";
import type { AutonomySuggestion } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";

interface SuggestionCardProps {
  suggestion: AutonomySuggestion;
  onConfirm: (id: string) => void;
  onDismiss: (id: string) => void;
  isPending: boolean;
}

function VelocityIndicator({ suggestion }: { suggestion: AutonomySuggestion }) {
  const velocity = suggestion.velocity;
  if (!velocity || velocity.sample_count === 0) return null;

  const label =
    velocity.avg_seconds !== undefined && velocity.avg_seconds !== null
      ? velocity.avg_seconds < 60
        ? `${Math.round(velocity.avg_seconds)}s avg`
        : `${Math.round(velocity.avg_seconds / 60)}m avg`
      : null;

  return (
    <div className="flex items-center gap-1 text-xs text-muted-foreground">
      <Clock className="h-3 w-3" />
      {velocity.fast_approval && (
        <Badge variant="secondary" className="text-xs px-1 py-0">
          Fast
        </Badge>
      )}
      {label && <span>{label} approval time</span>}
      <span className="text-muted-foreground/60">({velocity.sample_count} samples)</span>
    </div>
  );
}

function PromotionCard({ suggestion, onConfirm, onDismiss, isPending }: SuggestionCardProps) {
  return (
    <Card className="border-blue-200 bg-blue-50/30 dark:border-blue-800 dark:bg-blue-950/20">
      <CardHeader className="pb-2">
        <div className="flex items-start justify-between gap-2">
          <div className="flex items-center gap-2">
            <TrendingUp className="h-4 w-4 text-blue-600 dark:text-blue-400 shrink-0 mt-0.5" />
            <CardTitle className="text-sm font-semibold text-blue-900 dark:text-blue-100">
              Promote to Standing Rule
            </CardTitle>
          </div>
          <Badge variant="outline" className="text-xs shrink-0">
            {suggestion.approval_count_at_creation}× approved
          </Badge>
        </div>
      </CardHeader>
      <CardContent className="pb-2 space-y-2">
        <p className="text-sm text-foreground/80 font-mono bg-muted/50 rounded px-2 py-1">
          {suggestion.scope_description}
        </p>
        <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
          <span>Tool: {suggestion.tool_name}</span>
          <span>·</span>
          <span>
            Created{" "}
            {formatDistanceToNow(new Date(suggestion.created_at), { addSuffix: true })}
          </span>
          <VelocityIndicator suggestion={suggestion} />
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
          Confirm Rule
        </Button>
        <Button
          size="sm"
          variant="outline"
          onClick={() => onDismiss(suggestion.id)}
          disabled={isPending}
        >
          <X className="h-3.5 w-3.5 mr-1.5" />
          Dismiss
        </Button>
      </CardFooter>
    </Card>
  );
}

function DemotionCard({ suggestion, onConfirm, onDismiss, isPending }: SuggestionCardProps) {
  return (
    <Card className="border-amber-200 bg-amber-50/30 dark:border-amber-800 dark:bg-amber-950/20">
      <CardHeader className="pb-2">
        <div className="flex items-start justify-between gap-2">
          <div className="flex items-center gap-2">
            <AlertTriangle className="h-4 w-4 text-amber-600 dark:text-amber-400 shrink-0 mt-0.5" />
            <CardTitle className="text-sm font-semibold text-amber-900 dark:text-amber-100">
              Review Standing Rule
            </CardTitle>
          </div>
          <Badge variant="destructive" className="text-xs shrink-0">
            Execution failed
          </Badge>
        </div>
      </CardHeader>
      <CardContent className="pb-2 space-y-2">
        <p className="text-sm text-foreground/80 font-mono bg-muted/50 rounded px-2 py-1">
          {suggestion.scope_description}
        </p>
        <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
          <span>Tool: {suggestion.tool_name}</span>
          <span>·</span>
          <span>
            Created{" "}
            {formatDistanceToNow(new Date(suggestion.created_at), { addSuffix: true })}
          </span>
        </div>
      </CardContent>
      <CardFooter className="pt-0 gap-2">
        <Button
          size="sm"
          variant="destructive"
          onClick={() => onConfirm(suggestion.id)}
          disabled={isPending}
        >
          <AlertTriangle className="h-3.5 w-3.5 mr-1.5" />
          Revoke Rule
        </Button>
        <Button
          size="sm"
          variant="outline"
          onClick={() => onDismiss(suggestion.id)}
          disabled={isPending}
        >
          <X className="h-3.5 w-3.5 mr-1.5" />
          Keep Rule
        </Button>
      </CardFooter>
    </Card>
  );
}

interface AutonomySuggestionsBannerProps {
  suggestions: AutonomySuggestion[];
  onConfirm: (id: string) => void;
  onDismiss: (id: string) => void;
  /** IDs of suggestions currently being actioned */
  pendingIds?: Set<string>;
}

/**
 * Renders a banner of pending autonomy suggestion cards.
 * Returns null when there are no pending suggestions to show.
 */
export function AutonomySuggestionsBanner({
  suggestions,
  onConfirm,
  onDismiss,
  pendingIds = new Set(),
}: AutonomySuggestionsBannerProps) {
  const pending = suggestions.filter((s) => s.status === "pending");
  if (pending.length === 0) return null;

  return (
    <div className="space-y-3" data-testid="autonomy-suggestions-banner">
      <div className="flex items-center gap-2">
        <h2 className="text-sm font-semibold text-foreground/80">Autonomy Suggestions</h2>
        <Badge variant="secondary" className="text-xs">
          {pending.length}
        </Badge>
      </div>
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {pending.map((suggestion) =>
          suggestion.suggestion_type === "demotion" ? (
            <DemotionCard
              key={suggestion.id}
              suggestion={suggestion}
              onConfirm={onConfirm}
              onDismiss={onDismiss}
              isPending={pendingIds.has(suggestion.id)}
            />
          ) : (
            <PromotionCard
              key={suggestion.id}
              suggestion={suggestion}
              onConfirm={onConfirm}
              onDismiss={onDismiss}
              isPending={pendingIds.has(suggestion.id)}
            />
          ),
        )}
      </div>
    </div>
  );
}
