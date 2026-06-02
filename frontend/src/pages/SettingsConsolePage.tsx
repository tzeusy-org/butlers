/**
 * SettingsConsolePage — /settings  [bu-ju4kh §7.2]
 *
 * The Settings Console root. Aggregates sub-page summaries in a panel grid
 * and surfaces an AttentionStrip for items requiring human review.
 *
 * Layout:
 *   - Page heading (h1: "Settings")
 *   - AttentionStrip — attention items from GET /api/settings/console
 *   - Header KPI strip — 5 counts: active butlers, spend MTD, open approvals,
 *     models verified / total
 *   - Panel grid — one panel per sub-route; each fetches its own summary
 *     endpoint in parallel (a slow panel MUST NOT block the others)
 *
 * Design refs: §7.2, settings-redesign.jsx :: SettingsConsole
 * CSS: .attention-row[data-tone="red"|"amber"] from frontend/src/index.css
 *
 * bu-ju4kh — Phase 5: /settings Console
 */

import { useEffect, useState } from "react";
import { useNavigate } from "react-router";
import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "@/api/client";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface AttentionItem {
  tone: "red" | "amber";
  kind: string;
  text: string;
  action_route: string;
}

interface HeaderCounts {
  active_butlers: number;
  spend_mtd_usd: number;
  open_approvals: number;
  models_verified: number;
  models_total: number;
}

interface ConsoleData {
  header_counts: HeaderCounts;
  attention: AttentionItem[];
  attention_truncated_count: number;
}

// Minimal types for per-panel summaries (pulled from their own endpoints)

interface SpendSummary {
  total_cost_usd: number;
}

interface ModelStats {
  total: number;
  ok: number;
  errors: number;
}

interface ApprovalMetricsSummary {
  pending: number;
}

// ---------------------------------------------------------------------------
// API helpers — each panel fetches independently
// ---------------------------------------------------------------------------

function fetchConsole(): Promise<{ data: ConsoleData }> {
  return apiFetch<{ data: ConsoleData }>("/settings/console");
}

function fetchSpendSummary(): Promise<{ data: SpendSummary }> {
  return apiFetch<{ data: SpendSummary }>("/spend?period=30d");
}

function fetchModelStats(): Promise<ModelStats> {
  return apiFetch<{ data: Array<{ last_verified_ok: boolean | null; enabled: boolean }> }>(
    "/settings/models",
  ).then((res) => {
    const enabled = res.data.filter((m) => m.enabled);
    const ok = enabled.filter((m) => m.last_verified_ok === true).length;
    const errors = enabled.filter((m) => m.last_verified_ok === false).length;
    return { total: enabled.length, ok, errors };
  });
}

function fetchApprovalMetrics(): Promise<ApprovalMetricsSummary> {
  return apiFetch<{ data: { total_pending?: number } }>("/approvals/metrics").then((res) => ({
    pending: res.data?.total_pending ?? 0,
  }));
}

// ---------------------------------------------------------------------------
// AttentionStrip
// ---------------------------------------------------------------------------

function AttentionStrip({
  items,
  truncatedCount,
  onNavigate,
}: {
  items: AttentionItem[];
  truncatedCount: number;
  onNavigate: (route: string) => void;
}) {
  if (items.length === 0) {
    return (
      <p className="font-serif italic text-muted-foreground text-sm">
        Everything is in hand.
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-0 rounded-md overflow-hidden border border-border">
      {items.map((item, idx) => (
        <div
          key={`${item.kind}-${idx}`}
          className="attention-row flex items-center justify-between gap-4 px-4 py-3"
          data-tone={item.tone}
          role="alert"
          aria-label={item.text}
        >
          <div className="flex items-center gap-3 min-w-0">
            <span
              className={[
                "shrink-0 h-2 w-2 rounded-full",
                item.tone === "red" ? "bg-[var(--red)]" : "bg-[var(--amber)]",
              ].join(" ")}
              aria-hidden
            />
            <p className="text-sm truncate">{item.text}</p>
          </div>
          <button
            onClick={() => onNavigate(item.action_route)}
            className="shrink-0 text-xs font-medium underline underline-offset-2 text-muted-foreground hover:text-foreground transition-colors cursor-pointer"
            aria-label={`Go to ${item.action_route}`}
          >
            Review →
          </button>
        </div>
      ))}
      {truncatedCount > 0 && (
        <div className="px-4 py-2 text-xs text-muted-foreground bg-muted/40 border-t border-border flex items-center justify-between">
          <span>{truncatedCount} more item{truncatedCount !== 1 ? "s" : ""} not shown.</span>
          <button
            onClick={() => onNavigate("/audit-log")}
            className="underline underline-offset-2 hover:text-foreground transition-colors cursor-pointer"
          >
            ...{truncatedCount} more →
          </button>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Clock — mono HH:MM 24h, tabular nums, updates every minute
// ---------------------------------------------------------------------------

function ConsoleClock() {
  const [time, setTime] = useState(() => {
    const now = new Date();
    return now.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", hour12: false });
  });

  useEffect(() => {
    function tick() {
      const now = new Date();
      setTime(now.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", hour12: false }));
    }
    // Align to the next minute boundary
    const msUntilNextMinute = (60 - new Date().getSeconds()) * 1000;
    const timeout = setTimeout(() => {
      tick();
      const interval = setInterval(tick, 60_000);
      return () => clearInterval(interval);
    }, msUntilNextMinute);
    return () => clearTimeout(timeout);
  }, []);

  return (
    <span className="font-mono text-sm tabular-nums text-muted-foreground" aria-label="Current time">
      {time}
    </span>
  );
}

// ---------------------------------------------------------------------------
// KPI strip
// ---------------------------------------------------------------------------

function KpiCell({
  label,
  value,
  loading,
}: {
  label: string;
  value: React.ReactNode;
  loading?: boolean;
}) {
  return (
    <div className="flex flex-col gap-0.5">
      <p className="text-xs text-muted-foreground font-medium uppercase tracking-wider">{label}</p>
      {loading ? (
        <Skeleton className="h-7 w-16" />
      ) : (
        <p className="text-2xl font-bold tabular-nums">{value}</p>
      )}
    </div>
  );
}

function KpiStrip({ counts, loading }: { counts: HeaderCounts | undefined; loading: boolean }) {
  return (
    <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5 rounded-lg border border-border bg-card px-6 py-4">
      <KpiCell
        label="Active Butlers"
        value={counts?.active_butlers ?? 0}
        loading={loading}
      />
      <KpiCell
        label="Spend MTD"
        value={counts ? `$${counts.spend_mtd_usd.toFixed(2)}` : "$0.00"}
        loading={loading}
      />
      <KpiCell
        label="Open Approvals"
        value={counts?.open_approvals ?? 0}
        loading={loading}
      />
      <KpiCell
        label="Models OK"
        value={counts ? `${counts.models_verified}/${counts.models_total}` : "—"}
        loading={loading}
      />
      <KpiCell
        label="Status"
        value={
          counts ? (
            <Badge variant={counts.open_approvals > 0 ? "destructive" : "default"}>
              {counts.open_approvals > 0 ? "Needs Review" : "Nominal"}
            </Badge>
          ) : (
            "—"
          )
        }
        loading={loading}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Individual console panels
// ---------------------------------------------------------------------------

function PanelShell({
  title,
  description,
  href,
  children,
  onNavigate,
}: {
  title: string;
  description: string;
  href: string;
  children: React.ReactNode;
  onNavigate: (route: string) => void;
}) {
  return (
    <Card
      className="cursor-pointer hover:border-foreground/30 transition-colors"
      onClick={() => onNavigate(href)}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") onNavigate(href);
      }}
      aria-label={`Go to ${title}`}
    >
      <CardHeader className="pb-2">
        <CardTitle className="text-base">{title}</CardTitle>
        <CardDescription>{description}</CardDescription>
      </CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  );
}

// Models panel — independent fetch
function ModelsPanel({ onNavigate }: { onNavigate: (route: string) => void }) {
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ["console-panel-models"],
    queryFn: fetchModelStats,
    retry: 1,
  });

  return (
    <PanelShell
      title="Models"
      description="Catalog health and verification status."
      href="/settings/models"
      onNavigate={onNavigate}
    >
      {isLoading ? (
        <Skeleton className="h-10 w-full" />
      ) : isError ? (
        <p className="text-sm text-muted-foreground">
          Failed to load.{" "}
          <button
            onClick={(e) => {
              e.stopPropagation();
              refetch();
            }}
            className="underline underline-offset-2 hover:text-foreground transition-colors cursor-pointer"
          >
            Retry →
          </button>
        </p>
      ) : (
        <div className="flex items-baseline gap-4">
          <span className="text-2xl font-bold tabular-nums">{data?.ok ?? 0}</span>
          <span className="text-sm text-muted-foreground">
            verified / {data?.total ?? 0} enabled
          </span>
          {(data?.errors ?? 0) > 0 && (
            <Badge variant="destructive" className="ml-auto">
              {data!.errors} error{data!.errors !== 1 ? "s" : ""}
            </Badge>
          )}
        </div>
      )}
    </PanelShell>
  );
}

// Spend panel — independent fetch
function SpendPanel({ onNavigate }: { onNavigate: (route: string) => void }) {
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ["console-panel-spend"],
    queryFn: fetchSpendSummary,
    retry: 1,
  });

  return (
    <PanelShell
      title="Spend"
      description="Monthly cost tracking and forecast."
      href="/settings/spend"
      onNavigate={onNavigate}
    >
      {isLoading ? (
        <Skeleton className="h-10 w-full" />
      ) : isError ? (
        <p className="text-sm text-muted-foreground">
          Failed to load.{" "}
          <button
            onClick={(e) => {
              e.stopPropagation();
              refetch();
            }}
            className="underline underline-offset-2 hover:text-foreground transition-colors cursor-pointer"
          >
            Retry →
          </button>
        </p>
      ) : (
        <div className="flex items-baseline gap-2">
          <span className="text-2xl font-bold tabular-nums">
            ${(data?.data?.total_cost_usd ?? 0).toFixed(2)}
          </span>
          <span className="text-sm text-muted-foreground">MTD</span>
        </div>
      )}
    </PanelShell>
  );
}

// Approvals panel — independent fetch
function ApprovalsPanel({ onNavigate }: { onNavigate: (route: string) => void }) {
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ["console-panel-approvals"],
    queryFn: fetchApprovalMetrics,
    retry: 1,
  });

  return (
    <PanelShell
      title="Approvals"
      description="Pending actions awaiting your decision."
      href="/approvals"
      onNavigate={onNavigate}
    >
      {isLoading ? (
        <Skeleton className="h-10 w-full" />
      ) : isError ? (
        <p className="text-sm text-muted-foreground">
          Failed to load.{" "}
          <button
            onClick={(e) => {
              e.stopPropagation();
              refetch();
            }}
            className="underline underline-offset-2 hover:text-foreground transition-colors cursor-pointer"
          >
            Retry →
          </button>
        </p>
      ) : (
        <div className="flex items-baseline gap-2">
          <span
            className={[
              "text-2xl font-bold tabular-nums",
              (data?.pending ?? 0) > 0 ? "text-destructive" : "",
            ].join(" ")}
          >
            {data?.pending ?? 0}
          </span>
          <span className="text-sm text-muted-foreground">
            {(data?.pending ?? 0) === 1 ? "approval" : "approvals"} pending
          </span>
        </div>
      )}
    </PanelShell>
  );
}

// Permissions panel — static summary, no sub-fetch needed
function PermissionsPanel({ onNavigate }: { onNavigate: (route: string) => void }) {
  return (
    <PanelShell
      title="Permissions"
      description="Butler × permission matrix, webhooks, and data ops."
      href="/settings/permissions"
      onNavigate={onNavigate}
    >
      <p className="text-sm text-muted-foreground">
        Manage access policies, webhook integrations, and export or wipe controls.
      </p>
    </PanelShell>
  );
}

// Owner panel — static summary
function OwnerConfigPanel({ onNavigate }: { onNavigate: (route: string) => void }) {
  return (
    <PanelShell
      title="Owner Config"
      description="Primary identity and OAuth provider setup."
      href="/settings/owner"
      onNavigate={onNavigate}
    >
      <p className="text-sm text-muted-foreground">
        Configure Google OAuth app credentials and re-authorize owner accounts.
      </p>
    </PanelShell>
  );
}

// Secrets panel — static summary
function SecretsPanel({ onNavigate }: { onNavigate: (route: string) => void }) {
  return (
    <PanelShell
      title="Secrets"
      description="Credential inventory, probes, and audit history."
      href="/secrets"
      onNavigate={onNavigate}
    >
      <p className="text-sm text-muted-foreground">
        Inspect API keys, CLI tokens, user credentials, and what each credential feeds.
      </p>
    </PanelShell>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function SettingsConsolePage() {
  const navigate = useNavigate();

  const {
    data: consoleResp,
    isLoading: consoleLoading,
    isError: consoleError,
    refetch: consoleRefetch,
  } = useQuery({
    queryKey: ["settings-console"],
    queryFn: fetchConsole,
    staleTime: 10_000,
    refetchInterval: 30_000,
  });

  const consoleData = consoleResp?.data;

  function handleNavigate(route: string) {
    navigate(route);
  }

  return (
    <div className="space-y-6">
      {/* ------------------------------------------------------------------ */}
      {/* Page heading                                                         */}
      {/* ------------------------------------------------------------------ */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground mb-2">
            system · console
          </p>
          <h1 className="text-3xl font-bold tracking-tight">Settings</h1>
          <p className="text-muted-foreground mt-1 text-sm">
            System configuration, model catalog, spend controls, and access management.
          </p>
        </div>
        <ConsoleClock />
      </div>

      {/* ------------------------------------------------------------------ */}
      {/* AttentionStrip                                                        */}
      {/* ------------------------------------------------------------------ */}
      {consoleLoading ? (
        <div className="space-y-1">
          <Skeleton className="h-12 w-full" />
          <Skeleton className="h-12 w-full" />
        </div>
      ) : consoleError ? (
        <div
          className="attention-row rounded-md px-4 py-3 flex items-center justify-between"
          data-tone="amber"
          role="alert"
        >
          <p className="text-sm">Could not load console status.</p>
          <button
            onClick={() => consoleRefetch()}
            className="text-xs underline underline-offset-2 text-muted-foreground hover:text-foreground transition-colors cursor-pointer"
          >
            Retry →
          </button>
        </div>
      ) : consoleData ? (
        <AttentionStrip
          items={consoleData.attention}
          truncatedCount={consoleData.attention_truncated_count}
          onNavigate={handleNavigate}
        />
      ) : null}

      {/* ------------------------------------------------------------------ */}
      {/* Header KPI strip                                                     */}
      {/* ------------------------------------------------------------------ */}
      <KpiStrip counts={consoleData?.header_counts} loading={consoleLoading} />

      {/* ------------------------------------------------------------------ */}
      {/* Panel grid — each panel fetches its own data independently          */}
      {/* ------------------------------------------------------------------ */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {/* Models — independent fetch, failures do not block other panels */}
        <ModelsPanel onNavigate={handleNavigate} />

        {/* Spend — independent fetch */}
        <SpendPanel onNavigate={handleNavigate} />

        {/* Approvals — independent fetch */}
        <ApprovalsPanel onNavigate={handleNavigate} />

        {/* Permissions — static panel (navigates to sub-route) */}
        <PermissionsPanel onNavigate={handleNavigate} />

        {/* Owner Config — static panel */}
        <OwnerConfigPanel onNavigate={handleNavigate} />

        {/* Secrets — static panel */}
        <SecretsPanel onNavigate={handleNavigate} />
      </div>
    </div>
  );
}
