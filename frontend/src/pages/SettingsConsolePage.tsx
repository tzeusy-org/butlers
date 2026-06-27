/**
 * SettingsConsolePage — /settings  [bu-ju4kh §7.2]
 *
 * The Settings Console root. Aggregates sub-page summaries in a panel grid
 * and surfaces an AttentionStrip for items requiring human review.
 *
 * Layout:
 *   - Page heading (h1: "Settings")
 *   - AttentionStrip — attention items from GET /api/settings/console
 *   - Header KPI strip — 4 counts: active butlers, spend MTD, open approvals,
 *     models verified / total
 *   - Panel grid — one panel per sub-route; each fetches its own summary
 *     endpoint in parallel (a slow panel MUST NOT block the others)
 *
 * Design language: Dispatch. No card chrome, no word-badges — state is a
 * {dot, numeral, colour} only when state demands. Display weight 500 (never
 * 700). Numerals are tabular. Mirrors the SettingsModelsPage treatment and
 * the shared atoms in components/butler-detail/atoms.tsx.
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
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import { useSettingsConsoleStream } from "@/hooks/use-settings-console-stream";

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
// Shared mono eyebrow — 10px uppercase, 0.14em tracking, muted
// ---------------------------------------------------------------------------

function Eyebrow({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <p
      className={cn(
        "font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground leading-none",
        className,
      )}
    >
      {children}
    </p>
  );
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
    <div className="flex flex-col gap-0 overflow-hidden border border-border">
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
              className={cn(
                "shrink-0 h-2 w-2 rounded-full",
                item.tone === "red" ? "bg-[var(--red)]" : "bg-[var(--amber)]",
              )}
              aria-hidden
            />
            <p className="text-sm truncate">{item.text}</p>
          </div>
          <button
            onClick={() => onNavigate(item.action_route)}
            className="shrink-0 font-mono text-[11px] uppercase tracking-wider underline underline-offset-2 text-muted-foreground hover:text-foreground transition-colors cursor-pointer"
            aria-label={`Go to ${item.action_route}`}
          >
            Review →
          </button>
        </div>
      ))}
      {truncatedCount > 0 && (
        <div className="px-4 py-2 font-mono text-[11px] text-muted-foreground border-t border-border flex items-center justify-between">
          <span>{truncatedCount} more item{truncatedCount !== 1 ? "s" : ""} not shown.</span>
          <button
            onClick={() => onNavigate("/audit-log")}
            className="uppercase tracking-wider underline underline-offset-2 hover:text-foreground transition-colors cursor-pointer"
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
// KPI strip — hairline-divided, no card chrome. Mega numerals are weight 500,
// tabular. State colour appears only when state demands (open approvals > 0).
// ---------------------------------------------------------------------------

function KpiCell({
  label,
  value,
  tone = "fg",
  loading,
}: {
  label: string;
  value: React.ReactNode;
  tone?: "fg" | "red";
  loading?: boolean;
}) {
  return (
    <div className="flex flex-col gap-1.5 px-4 py-3 border-r border-b border-border/60 last:border-r-0 sm:[&:nth-child(2)]:border-r-0 lg:[&:nth-child(2)]:border-r lg:[&:nth-child(4)]:border-r-0">
      <Eyebrow>{label}</Eyebrow>
      {loading ? (
        <Skeleton className="h-8 w-16" />
      ) : (
        <span
          className={cn(
            "text-[28px] font-medium tracking-tight tabular-nums leading-none",
            tone === "red" ? "text-[var(--red)]" : "text-foreground",
          )}
        >
          {value}
        </span>
      )}
    </div>
  );
}

function KpiStrip({ counts, loading }: { counts: HeaderCounts | undefined; loading: boolean }) {
  const openApprovals = counts?.open_approvals ?? 0;
  return (
    <div className="grid grid-cols-2 lg:grid-cols-4 border-t border-l border-border/60">
      <KpiCell label="Active Butlers" value={counts?.active_butlers ?? 0} loading={loading} />
      <KpiCell
        label="Spend MTD"
        value={counts ? `$${counts.spend_mtd_usd.toFixed(2)}` : "$0.00"}
        loading={loading}
      />
      <KpiCell
        label="Open Approvals"
        value={openApprovals}
        tone={openApprovals > 0 ? "red" : "fg"}
        loading={loading}
      />
      <KpiCell
        label="Models OK"
        value={counts ? `${counts.models_verified}/${counts.models_total}` : "—"}
        loading={loading}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Individual console panels — hairline cells, no card chrome
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
    <div
      className="group flex flex-col gap-3 border-r border-b border-border/60 px-4 py-4 cursor-pointer transition-colors hover:bg-muted/30"
      onClick={() => onNavigate(href)}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") onNavigate(href);
      }}
      aria-label={`Go to ${title}`}
    >
      <div className="flex flex-col gap-1">
        <div className="flex items-baseline justify-between gap-2">
          <span className="text-base font-medium tracking-tight">{title}</span>
          <span
            className="font-mono text-xs text-muted-foreground opacity-0 group-hover:opacity-100 transition-opacity"
            aria-hidden
          >
            →
          </span>
        </div>
        <p className="text-xs text-muted-foreground leading-relaxed">{description}</p>
      </div>
      {children}
    </div>
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
        <Skeleton className="h-8 w-full" />
      ) : isError ? (
        <p className="text-sm text-muted-foreground">
          Failed to load.{" "}
          <button
            onClick={(e) => {
              e.stopPropagation();
              refetch();
            }}
            className="font-mono text-[11px] uppercase tracking-wider underline underline-offset-2 hover:text-foreground transition-colors cursor-pointer"
          >
            Retry →
          </button>
        </p>
      ) : (
        <div className="flex items-baseline gap-2">
          <span className="text-[22px] font-medium tabular-nums leading-none">{data?.ok ?? 0}</span>
          <span className="text-xs text-muted-foreground">
            verified / {data?.total ?? 0} enabled
          </span>
          {(data?.errors ?? 0) > 0 && (
            <span className="ml-auto inline-flex items-center gap-1.5 font-mono text-xs tabular-nums text-[var(--red)]">
              <span className="h-1.5 w-1.5 rounded-full bg-[var(--red)]" aria-hidden />
              {data!.errors} error{data!.errors !== 1 ? "s" : ""}
            </span>
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
        <Skeleton className="h-8 w-full" />
      ) : isError ? (
        <p className="text-sm text-muted-foreground">
          Failed to load.{" "}
          <button
            onClick={(e) => {
              e.stopPropagation();
              refetch();
            }}
            className="font-mono text-[11px] uppercase tracking-wider underline underline-offset-2 hover:text-foreground transition-colors cursor-pointer"
          >
            Retry →
          </button>
        </p>
      ) : (
        <div className="flex items-baseline gap-2">
          <span className="text-[22px] font-medium tabular-nums leading-none">
            ${(data?.data?.total_cost_usd ?? 0).toFixed(2)}
          </span>
          <span className="text-xs text-muted-foreground">MTD</span>
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

  const pending = data?.pending ?? 0;

  return (
    <PanelShell
      title="Approvals"
      description="Pending actions awaiting your decision."
      href="/approvals"
      onNavigate={onNavigate}
    >
      {isLoading ? (
        <Skeleton className="h-8 w-full" />
      ) : isError ? (
        <p className="text-sm text-muted-foreground">
          Failed to load.{" "}
          <button
            onClick={(e) => {
              e.stopPropagation();
              refetch();
            }}
            className="font-mono text-[11px] uppercase tracking-wider underline underline-offset-2 hover:text-foreground transition-colors cursor-pointer"
          >
            Retry →
          </button>
        </p>
      ) : (
        <div className="flex items-baseline gap-2">
          <span
            className={cn(
              "text-[22px] font-medium tabular-nums leading-none",
              pending > 0 ? "text-[var(--red)]" : "text-foreground",
            )}
          >
            {pending}
          </span>
          <span className="text-xs text-muted-foreground">
            {pending === 1 ? "approval" : "approvals"} pending
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
      <p className="text-xs text-muted-foreground leading-relaxed">
        Manage access policies, webhook integrations, and export or wipe controls.
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
      <p className="text-xs text-muted-foreground leading-relaxed">
        Inspect API keys, CLI tokens, user credentials, and the Google OAuth app
        configuration — plus what each credential feeds.
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
    // The WS /api/settings/stream ticker drives live updates; this poll is only
    // a slow cold-start / reconnect safety net, not the primary update path.
    refetchInterval: 5 * 60_000,
  });

  // Live console state: the WS sends a full snapshot on connect, then applies
  // header_delta / attention_add / attention_remove events incrementally
  // (spec: dashboard-settings-console — Settings Console Live Stream). Until the
  // first snapshot arrives (or if the socket is down) we fall back to the GET
  // fetch above.
  const { data: liveConsoleData, status: streamStatus } = useSettingsConsoleStream();

  // Prefer the live stream while it is connecting/open. If the socket is down
  // (e.g. a proxy/firewall blocks the WS permanently), `liveConsoleData` would
  // otherwise stay frozen at its last value forever; in that case fall back to
  // the periodic GET poll so the safety net actually refreshes the view.
  const consoleData =
    streamStatus === "closed"
      ? (consoleResp?.data ?? liveConsoleData)
      : (liveConsoleData ?? consoleResp?.data);

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
          <Eyebrow className="mb-2">system · console</Eyebrow>
          <h1 className="text-3xl font-medium tracking-tight leading-tight">Settings</h1>
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
          className="attention-row px-4 py-3 flex items-center justify-between"
          data-tone="amber"
          role="alert"
        >
          <p className="text-sm">Could not load console status.</p>
          <button
            onClick={() => consoleRefetch()}
            className="font-mono text-[11px] uppercase tracking-wider underline underline-offset-2 text-muted-foreground hover:text-foreground transition-colors cursor-pointer"
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
      {/* Hairline frame: outer border-t/border-l, cells border-r/border-b.   */}
      {/* ------------------------------------------------------------------ */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 border-t border-l border-border/60">
        {/* Models — independent fetch, failures do not block other panels */}
        <ModelsPanel onNavigate={handleNavigate} />

        {/* Spend — independent fetch */}
        <SpendPanel onNavigate={handleNavigate} />

        {/* Approvals — independent fetch */}
        <ApprovalsPanel onNavigate={handleNavigate} />

        {/* Permissions — static panel (navigates to sub-route) */}
        <PermissionsPanel onNavigate={handleNavigate} />

        {/* Secrets — static panel (Google OAuth app credentials now live here) */}
        <SecretsPanel onNavigate={handleNavigate} />
      </div>
    </div>
  );
}
