/**
 * AutonomyPanel — the always-visible approval-gate baseline.
 *
 * The dashboard must show both standing grants and configured tools that
 * still require a decision.  A rule-only ledger hides the latter and can
 * misleadingly imply that an absent tool is ungated.
 */

import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { getApprovalGatedTools } from "@/api/index.ts";
import type { ApprovalGatedTool, ApprovalRule } from "@/api/index.ts";
import { approvalKeys, useRevokeRule } from "@/hooks/use-approvals.ts";
import { QueryBoundary, SourceDegradedNote } from "@/components/ui/query-boundary.tsx";
import { CreateRuleDialog } from "@/components/approvals/create-rule-dialog.tsx";

// Live use counts: reconcile periodically so grants created or revoked
// elsewhere appear without requiring a page reload.
const GATED_TOOLS_REFETCH_MS = 20_000;

function fmtTs(iso: string | null | undefined): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleDateString(undefined, {
      month: "short",
      day: "numeric",
    });
  } catch {
    return iso;
  }
}

function isWildcardConstraints(constraints: Record<string, unknown>): boolean {
  if (Object.keys(constraints).length === 0) return true;
  return constraints.type === "any";
}

function ruleTier(rule: ApprovalRule): "full autonomy" | "scoped" {
  return isWildcardConstraints(rule.arg_constraints) ? "full autonomy" : "scoped";
}

function tierClass(tier: "full autonomy" | "scoped"): string {
  return tier === "full autonomy"
    ? "text-[var(--red-text)]"
    : "text-blue-600 dark:text-blue-400";
}

function RuleRow({ rule }: { rule: ApprovalRule }) {
  const [confirming, setConfirming] = useState(false);
  const revokeMut = useRevokeRule();
  const tier = ruleTier(rule);
  const nearMaxUses = rule.max_uses != null && rule.use_count >= rule.max_uses;

  return (
    <div className="mt-2 border-t border-border/50 pt-2">
      <div className="flex items-center justify-between gap-2">
        <span className={`font-mono text-[10px] uppercase tracking-wider ${tierClass(tier)}`}>
          {tier}
        </span>
        {!confirming ? (
          <button
            onClick={() => setConfirming(true)}
            className="shrink-0 text-[10px] font-mono uppercase tracking-wide text-muted-foreground hover:text-destructive transition-colors"
          >
            Revoke
          </button>
        ) : (
          <div className="shrink-0 flex items-center gap-1.5">
            <span className="text-[10px] font-mono text-muted-foreground">Revoke?</span>
            <button
              onClick={() => {
                revokeMut.mutate(rule.id, { onSettled: () => setConfirming(false) });
              }}
              disabled={revokeMut.isPending}
              className="text-[10px] font-mono uppercase tracking-wide text-destructive hover:underline disabled:opacity-50"
            >
              {revokeMut.isPending ? "revoking…" : "confirm"}
            </button>
            <button
              onClick={() => setConfirming(false)}
              className="text-[10px] font-mono uppercase tracking-wide text-muted-foreground hover:underline"
            >
              cancel
            </button>
          </div>
        )}
      </div>
      <div className="mt-0.5 text-xs text-muted-foreground line-clamp-2">
        {rule.description}
      </div>
      <div className="mt-1 font-mono text-[10px] text-muted-foreground">
        <span className={nearMaxUses ? "text-[var(--amber-text)] font-medium" : undefined}>
          {rule.use_count} use{rule.use_count === 1 ? "" : "s"}
          {rule.max_uses != null ? ` / ${rule.max_uses}` : ""}
        </span>
        {rule.expires_at && <span> · expires {fmtTs(rule.expires_at)}</span>}
      </div>
    </div>
  );
}

function ToolRow({
  tool,
  ruleStateUnavailable,
}: {
  tool: ApprovalGatedTool;
  ruleStateUnavailable: boolean;
}) {
  const ruleCount = tool.active_rules.length;
  const status = ruleStateUnavailable
    ? "rule state unavailable"
    : ruleCount === 0
      ? "always ask"
      : `${ruleCount} standing rule${ruleCount === 1 ? "" : "s"}`;

  return (
    <div className="py-2.5 border-b border-border/50 last:border-b-0">
      <div className="flex items-center justify-between gap-2">
        <span className="font-mono text-xs text-foreground truncate">
          {tool.butler} · {tool.tool_name}
        </span>
        <span className="font-mono text-[10px] uppercase tracking-wider shrink-0 text-muted-foreground">
          {tool.risk_tier}
        </span>
      </div>
      <div className="mt-0.5 flex items-center justify-between gap-2 text-[10px] font-mono text-muted-foreground">
        <span className={status === "always ask" ? "text-[var(--amber-text)]" : undefined}>
          {status}
        </span>
        <span>expires in {tool.expiry_hours}h</span>
      </div>
      {!ruleStateUnavailable && tool.active_rules.map((rule) => <RuleRow key={rule.id} rule={rule} />)}
    </div>
  );
}

export function AutonomyPanel() {
  const qc = useQueryClient();
  const [createOpen, setCreateOpen] = useState(false);
  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: approvalKeys.gatedTools(),
    queryFn: getApprovalGatedTools,
    refetchInterval: GATED_TOOLS_REFETCH_MS,
  });

  const gatedTools = data?.data ?? [];
  const degradedSources = new Set(
    (data?.meta?.sources_degraded as string[] | undefined) ?? [],
  );

  return (
    <div
      className="w-full md:w-80 shrink-0 border-t md:border-t-0 md:border-l border-border overflow-y-auto flex flex-col max-h-64 md:max-h-none"
      data-testid="autonomy-panel"
    >
      <div className="px-4 pt-4 pb-3 border-b border-border flex items-center justify-between gap-2 shrink-0">
        <div>
          <div className="text-[10px] font-mono uppercase tracking-widest text-muted-foreground">
            Autonomy
          </div>
          <div className="text-xs text-muted-foreground mt-0.5">
            What the fleet may do unsupervised
          </div>
        </div>
        <button
          onClick={() => setCreateOpen(true)}
          className="shrink-0 text-[10px] font-mono uppercase tracking-wide px-2 py-1 border border-border rounded hover:border-foreground/40 transition-colors"
        >
          + Rule
        </button>
      </div>

      <div className="px-4 py-3 flex-1">
        <QueryBoundary
          isLoading={isLoading}
          isError={isError}
          error={error}
          isEmpty={gatedTools.length === 0}
          onRetry={() => void refetch()}
          sourceLabel="approval-gate baseline"
          loadingFallback={
            <div className="text-xs text-muted-foreground font-mono">loading…</div>
          }
          emptyFallback={
            degradedSources.size > 0 ? (
              <SourceDegradedNote
                label="Approval-gate baseline"
                detail={`${[...degradedSources].join(", ")} unavailable. Gate inventory may be incomplete.`}
                onRetry={() => void refetch()}
              />
            ) : (
              <div className="text-xs text-muted-foreground">
                No approval-gated tools are configured.
              </div>
            )
          }
        >
          {gatedTools.map((tool) => (
            <ToolRow
              key={`${tool.butler}:${tool.tool_name}`}
              tool={tool}
              ruleStateUnavailable={
                degradedSources.has(tool.butler) || degradedSources.has("approval_rules")
              }
            />
          ))}
        </QueryBoundary>
      </div>

      <CreateRuleDialog
        open={createOpen}
        onOpenChange={(open) => {
          setCreateOpen(open);
          if (!open) {
            void qc.invalidateQueries({ queryKey: approvalKeys.rules() });
            void qc.invalidateQueries({ queryKey: approvalKeys.gatedTools() });
          }
        }}
      />
    </div>
  );
}
