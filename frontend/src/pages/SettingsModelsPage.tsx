/**
 * SettingsModelsPage — /settings/models
 *
 * Renders the global model catalog in the Dispatch design language:
 *   - Tier-grouped sections in canonical order (reasoning, workhorse, cheap, specialty, local, legacy)
 *   - Per-row priority stepper (↑/↓) backed by PUT /api/settings/models/{id}/priority
 *   - Enable toggle backed by PUT /api/settings/models/{id}
 *   - Test / Edit / Delete row actions
 *   - State and tier filter chips
 *   - "Verify All" button backed by POST /api/settings/models/verify-all
 *   - Dev-mode ApiWireFooter showing endpoints this page hits (§4.5)
 *
 * Design refs:
 *   pr/overview/settings-refactor/settings-redesign.jsx :: ModelCatalogExpanded
 *
 * bu-q2nz3 — Phase 2: /settings/models page
 */

import { useState } from "react";
import { toast } from "sonner";

import { ApiError } from "@/api/index.ts";
import type { ComplexityTier, ModelCatalogEntry } from "@/api/types.ts";
import { Switch } from "@/components/ui/switch";
import { Button } from "@/components/ui/button";
import {
  useDeleteModelCatalogEntry,
  useModelCatalog,
  useTestModelCatalogEntry,
  useUpdateModelCatalogEntry,
  useUpdateModelPriority,
  useVerifyAllModels,
} from "@/hooks/use-model-catalog";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** Canonical tier order per spec §3.3 and design §D4. */
const TIER_ORDER: ComplexityTier[] = [
  "reasoning",
  "workhorse",
  "cheap",
  "specialty",
  "local",
  "legacy",
];

/** Human-readable tier labels. */
const TIER_LABEL: Record<ComplexityTier, string> = {
  reasoning: "Reasoning",
  workhorse: "Workhorse",
  cheap: "Cheap",
  specialty: "Specialty",
  local: "Local",
  legacy: "Legacy",
};

type StateFilter = "all" | "verified" | "attention" | "offline" | "deprecated";

// ---------------------------------------------------------------------------
// Filter chip sub-component
// ---------------------------------------------------------------------------

function FilterChip({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={[
        "px-2 py-0.5 rounded text-[10px] font-mono uppercase tracking-widest border",
        "transition-colors cursor-pointer",
        active
          ? "bg-foreground text-background border-foreground"
          : "bg-transparent text-muted-foreground border-border hover:border-foreground/40",
      ].join(" ")}
    >
      {children}
    </button>
  );
}

// ---------------------------------------------------------------------------
// Tier section header
// ---------------------------------------------------------------------------

function TierHeader({
  tier,
  count,
}: {
  tier: ComplexityTier;
  count: number;
}) {
  return (
    <div className="flex items-baseline gap-3 px-4 py-2 border-b border-border bg-muted/30">
      <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-foreground">
        {TIER_LABEL[tier]}
      </span>
      <span className="font-mono text-[10px] text-muted-foreground">
        {count} {count === 1 ? "model" : "models"}
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Priority stepper
// ---------------------------------------------------------------------------

function PriorityStepper({
  entryId,
  priority,
}: {
  entryId: string;
  priority: number;
}) {
  const updatePriority = useUpdateModelPriority();

  const step = (delta: number) => {
    updatePriority.mutate(
      { id: entryId, body: { delta } },
      {
        onError: () => toast.error("Failed to update priority"),
      },
    );
  };

  return (
    <div className="flex items-center gap-1">
      <button
        onClick={() => step(-1)}
        disabled={priority === 0 || updatePriority.isPending}
        className="w-5 h-5 flex items-center justify-center rounded font-mono text-xs
          border border-border text-muted-foreground hover:text-foreground
          hover:border-foreground/40 disabled:opacity-30 disabled:cursor-not-allowed
          transition-colors"
        title="Decrease priority"
      >
        ↓
      </button>
      <span className="font-mono text-[11px] w-6 text-center tabular-nums">
        {priority}
      </span>
      <button
        onClick={() => step(1)}
        disabled={updatePriority.isPending}
        className="w-5 h-5 flex items-center justify-center rounded font-mono text-xs
          border border-border text-muted-foreground hover:text-foreground
          hover:border-foreground/40 disabled:opacity-30 disabled:cursor-not-allowed
          transition-colors"
        title="Increase priority"
      >
        ↑
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Model row
// ---------------------------------------------------------------------------

function ModelRow({ model }: { model: ModelCatalogEntry }) {
  const updateEntry = useUpdateModelCatalogEntry();
  const testEntry = useTestModelCatalogEntry();
  const deleteEntry = useDeleteModelCatalogEntry();

  const toggleEnabled = () => {
    updateEntry.mutate(
      { id: model.id, body: { enabled: !model.enabled } },
      { onError: () => toast.error("Failed to toggle model") },
    );
  };

  const handleTest = () => {
    testEntry.mutate(model.id, {
      onSuccess: (resp) => {
        if (resp.data.success) {
          toast.success(`${model.alias}: OK (${resp.data.duration_ms}ms)`);
        } else {
          toast.error(`${model.alias}: ${resp.data.error ?? "test failed"}`);
        }
      },
      onError: () => toast.error(`Failed to test ${model.alias}`),
    });
  };

  const handleDelete = () => {
    if (!confirm(`Delete model "${model.alias}"? This cannot be undone.`)) return;
    deleteEntry.mutate(model.id, {
      onSuccess: () => toast.success(`Deleted ${model.alias}`),
      onError: () => toast.error(`Failed to delete ${model.alias}`),
    });
  };

  const verificationStatus = model.last_verified_ok === true
    ? "verified"
    : model.last_verified_ok === false
      ? "error"
      : "untested";

  return (
    <div
      className={[
        "grid items-center gap-3 px-4 py-2.5 border-b border-border/50",
        "text-sm transition-colors hover:bg-muted/20",
        !model.enabled && "opacity-60",
      ]
        .filter(Boolean)
        .join(" ")}
      style={{ gridTemplateColumns: "auto 1fr auto auto auto auto auto" }}
    >
      {/* Priority stepper */}
      <PriorityStepper entryId={model.id} priority={model.priority} />

      {/* Model info */}
      <div className="min-w-0">
        <div className="flex items-center gap-2 min-w-0">
          <span className="font-mono text-[12px] truncate">{model.alias}</span>
          {verificationStatus === "verified" && (
            <span className="font-mono text-[9px] uppercase tracking-widest text-green-600 dark:text-green-400 shrink-0">
              ✓
            </span>
          )}
          {verificationStatus === "error" && (
            <span className="font-mono text-[9px] uppercase tracking-widest text-destructive shrink-0">
              ✗
            </span>
          )}
        </div>
        <div className="font-mono text-[10px] text-muted-foreground truncate">
          {model.model_id} · {model.runtime_type}
        </div>
      </div>

      {/* Usage (24h) */}
      <span className="font-mono text-[10px] text-muted-foreground tabular-nums text-right">
        {model.usage_24h.toLocaleString()} tok
      </span>

      {/* Enable toggle */}
      <Switch
        checked={model.enabled}
        onCheckedChange={toggleEnabled}
        disabled={updateEntry.isPending}
        aria-label={`${model.enabled ? "Disable" : "Enable"} ${model.alias}`}
      />

      {/* Test action */}
      <button
        onClick={handleTest}
        disabled={testEntry.isPending}
        className="font-mono text-[10px] uppercase tracking-widest text-muted-foreground
          hover:text-foreground transition-colors disabled:opacity-40 whitespace-nowrap"
      >
        Test →
      </button>

      {/* Edit action (placeholder — full edit dialog is out of scope for Phase 2) */}
      <button
        onClick={() => toast.info(`Edit for ${model.alias} — coming soon`)}
        className="font-mono text-[10px] uppercase tracking-widest text-muted-foreground
          hover:text-foreground transition-colors whitespace-nowrap"
      >
        Edit →
      </button>

      {/* Delete action */}
      <button
        onClick={handleDelete}
        disabled={deleteEntry.isPending}
        className="font-mono text-[10px] uppercase tracking-widest text-muted-foreground
          hover:text-destructive transition-colors disabled:opacity-40 whitespace-nowrap"
      >
        Delete →
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Empty tier state (§4.4)
// ---------------------------------------------------------------------------

function EmptyTierState() {
  return (
    <div className="px-4 py-4">
      <p className="font-serif text-sm italic text-muted-foreground">
        Nothing in this tier.
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Dev-mode ApiWireFooter (§4.5)
// ---------------------------------------------------------------------------

function ApiWireFooter() {
  if (!import.meta.env.DEV) return null;

  const endpoints = [
    "GET /api/settings/models",
    "PUT /api/settings/models/{id}/priority",
    "PUT /api/settings/models/{id}",
    "POST /api/settings/models/{id}/test",
    "DELETE /api/settings/models/{id}",
    "POST /api/settings/models/verify-all",
  ];

  return (
    <div className="mt-8 px-4 py-3 border border-border/50 rounded bg-muted/20">
      <p className="font-mono text-[9px] uppercase tracking-widest text-muted-foreground mb-2">
        dev · api wire
      </p>
      <ul className="space-y-0.5">
        {endpoints.map((ep) => (
          <li key={ep} className="font-mono text-[10px] text-muted-foreground">
            {ep}
          </li>
        ))}
      </ul>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function SettingsModelsPage() {
  const [tierFilter, setTierFilter] = useState<ComplexityTier | "all">("all");
  const [stateFilter, setStateFilter] = useState<StateFilter>("all");

  const { data, isLoading, isError } = useModelCatalog();
  const verifyAll = useVerifyAllModels();

  const entries: ModelCatalogEntry[] = data?.data ?? [];

  // Group by tier preserving canonical order
  const grouped = Object.fromEntries(
    TIER_ORDER.map((t) => [t, [] as ModelCatalogEntry[]]),
  ) as Record<ComplexityTier, ModelCatalogEntry[]>;

  for (const entry of entries) {
    const tier = entry.complexity_tier;
    if (grouped[tier]) {
      grouped[tier].push(entry);
    }
  }

  // Apply state filter
  const applyStateFilter = (rows: ModelCatalogEntry[]) => {
    if (stateFilter === "all") return rows;
    if (stateFilter === "verified") return rows.filter((m) => m.last_verified_ok === true);
    if (stateFilter === "attention") {
      return rows.filter((m) => m.last_verified_ok === false || m.last_verified_ok === null);
    }
    // offline / deprecated — not yet surfaced in this iteration
    return rows;
  };

  const visibleTiers: ComplexityTier[] = TIER_ORDER.filter(
    (t) => tierFilter === "all" || tierFilter === t,
  );

  // Counts for filter chips
  const totalCount = entries.length;
  const verifiedCount = entries.filter((m) => m.last_verified_ok === true).length;
  const tierCounts = Object.fromEntries(
    TIER_ORDER.map((t) => [t, grouped[t].length]),
  ) as Record<ComplexityTier, number>;

  const handleVerifyAll = () => {
    verifyAll.mutate(undefined, {
      onSuccess: (resp) => {
        const { ok, failed, total } = resp.data;
        toast.success(`Verified ${ok}/${total} models${failed > 0 ? ` · ${failed} failed` : ""}`);
      },
      onError: (err) => {
        if (err instanceof ApiError && err.status === 429) {
          toast.warning("Verify all was called recently — wait 60 seconds before retrying.");
        } else {
          const msg = err instanceof Error ? err.message : "Verify all failed";
          toast.error(msg);
        }
      },
    });
  };

  return (
    <div className="flex flex-col min-h-screen">
      {/* Breadcrumb */}
      <div className="px-7 py-3.5 border-b border-border flex items-baseline gap-3 font-mono text-[10px] text-muted-foreground uppercase tracking-[0.14em]">
        <span>butlers</span>
        <span>›</span>
        <span className="text-foreground/70">settings</span>
        <span>›</span>
        <span className="text-foreground">model catalog</span>
        <span className="ml-auto font-mono text-[10px] normal-case tracking-[0.04em] text-muted-foreground">
          {totalCount} models · {verifiedCount} verified · {TIER_ORDER.length} tiers
        </span>
      </div>

      {/* Page header */}
      <div className="px-7 py-5 border-b border-border grid grid-cols-[1fr_auto] gap-6 items-baseline">
        <div>
          <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground mb-2">
            settings · §1 · model catalog
          </p>
          <h1 className="text-3xl font-medium tracking-tight leading-tight">
            Every model the staff can call.
          </h1>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={handleVerifyAll}
            disabled={verifyAll.isPending}
            className="font-mono text-[10px] uppercase tracking-widest"
          >
            {verifyAll.isPending ? "Verifying…" : "Verify all →"}
          </Button>
        </div>
      </div>

      {/* Filter bar */}
      <div className="px-7 py-2.5 border-b border-border flex items-center gap-4 flex-wrap font-mono text-[10px]">
        {/* Tier chips */}
        <div className="flex items-center gap-1.5">
          <span className="text-[9px] uppercase tracking-widest text-muted-foreground">tier</span>
          <FilterChip active={tierFilter === "all"} onClick={() => setTierFilter("all")}>
            all · {totalCount}
          </FilterChip>
          {TIER_ORDER.map((t) => (
            <FilterChip
              key={t}
              active={tierFilter === t}
              onClick={() => setTierFilter(t)}
            >
              {t} · {tierCounts[t]}
            </FilterChip>
          ))}
        </div>

        <div className="w-px h-4 bg-border" />

        {/* State chips */}
        <div className="flex items-center gap-1.5">
          <span className="text-[9px] uppercase tracking-widest text-muted-foreground">state</span>
          <FilterChip active={stateFilter === "all"} onClick={() => setStateFilter("all")}>
            all
          </FilterChip>
          <FilterChip
            active={stateFilter === "verified"}
            onClick={() => setStateFilter("verified")}
          >
            verified · {verifiedCount}
          </FilterChip>
          <FilterChip
            active={stateFilter === "attention"}
            onClick={() => setStateFilter("attention")}
          >
            attention
          </FilterChip>
        </div>
      </div>

      {/* Body */}
      <div className="flex-1 overflow-auto">
        {isLoading && (
          <div className="px-7 py-10">
            <p className="font-serif text-sm italic text-muted-foreground">Loading catalog…</p>
          </div>
        )}

        {isError && (
          <div className="px-7 py-10">
            <p className="font-serif text-sm italic text-destructive">
              Failed to load model catalog.
            </p>
          </div>
        )}

        {!isLoading && !isError && (
          <>
            {visibleTiers.map((tier) => {
              const rows = applyStateFilter(grouped[tier]);
              return (
                <section key={tier}>
                  <TierHeader tier={tier} count={grouped[tier].length} />
                  {rows.length === 0 ? (
                    <EmptyTierState />
                  ) : (
                    rows.map((model) => <ModelRow key={model.id} model={model} />)
                  )}
                </section>
              );
            })}

            {/* Footer */}
            <div className="px-7 py-5 flex items-baseline justify-between">
              <span className="font-mono text-[9.5px] text-muted-foreground normal-case tracking-[0.04em]">
                end of catalog · {totalCount} {totalCount === 1 ? "entry" : "entries"}
              </span>
              <button
                onClick={handleVerifyAll}
                disabled={verifyAll.isPending}
                className="font-mono text-[11px] text-muted-foreground hover:text-foreground
                  transition-colors disabled:opacity-40"
              >
                verify all now →
              </button>
            </div>
          </>
        )}

        <div className="px-7 pb-8">
          <ApiWireFooter />
        </div>
      </div>
    </div>
  );
}
