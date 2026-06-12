import { useState } from "react";
import MemoryOverture from "@/components/memory/MemoryOverture";
import MemoryTierCards from "@/components/memory/MemoryTierCards";
import MemoryBrowser from "@/components/memory/MemoryBrowser";
import AttentionRail from "@/components/memory/AttentionRail";
import ReembedPanel from "@/components/memory/ReembedPanel";
import {
  useMemoryRetentionPolicies,
  useUpdateMemoryRetentionPolicies,
  useMemoryCompactionLog,
} from "@/hooks/use-memory";
import type { MemoryRetentionPolicy } from "@/api/types.ts";

// ---------------------------------------------------------------------------
// Retention policy table (§10.4 §2)
// ---------------------------------------------------------------------------

function RetentionPolicyRow({
  policy,
  onChange,
}: {
  policy: MemoryRetentionPolicy;
  onChange: (kind: string, field: "ttl_days" | "max_rows", value: number | null) => void;
}) {
  return (
    <tr className="border-b">
      <td className="py-2 pr-4 font-mono text-sm">{policy.kind}</td>
      <td className="py-2 pr-4">
        <input
          type="number"
          min={1}
          placeholder="none"
          defaultValue={policy.ttl_days ?? ""}
          className="w-24 rounded border px-2 py-1 text-sm"
          onChange={(e) => {
            const val = e.target.value;
            const v = val === "" ? null : parseInt(val, 10);
            if (v !== null && isNaN(v)) return;
            onChange(policy.kind, "ttl_days", v);
          }}
        />
      </td>
      <td className="py-2 pr-4">
        <input
          type="number"
          min={1}
          placeholder="none"
          defaultValue={policy.max_rows ?? ""}
          className="w-28 rounded border px-2 py-1 text-sm"
          onChange={(e) => {
            const val = e.target.value;
            const v = val === "" ? null : parseInt(val, 10);
            if (v !== null && isNaN(v)) return;
            onChange(policy.kind, "max_rows", v);
          }}
        />
      </td>
      <td className="py-2 text-right text-xs text-muted-foreground">
        {policy.updated_by ?? "system"} &middot;{" "}
        {new Date(policy.updated_at).toLocaleDateString()}
      </td>
    </tr>
  );
}

function RetentionPoliciesSection() {
  const { data: policiesResp, isLoading } = useMemoryRetentionPolicies();
  const updateMutation = useUpdateMemoryRetentionPolicies();

  const policies = policiesResp?.data ?? [];

  // Local edits — track deltas; submit all on Save.
  const [edits, setEdits] = useState<
    Map<string, { ttl_days: number | null; max_rows: number | null }>
  >(new Map());

  function handleChange(
    kind: string,
    field: "ttl_days" | "max_rows",
    value: number | null,
  ) {
    setEdits((prev) => {
      const current = prev.get(kind) ?? {
        ttl_days: policies.find((p) => p.kind === kind)?.ttl_days ?? null,
        max_rows: policies.find((p) => p.kind === kind)?.max_rows ?? null,
      };
      return new Map(prev).set(kind, { ...current, [field]: value });
    });
  }

  function handleSave() {
    if (edits.size === 0) return;
    const entries = Array.from(edits.entries()).map(([kind, vals]) => ({
      kind,
      ...vals,
    }));
    updateMutation.mutate({ policies: entries });
    setEdits(new Map());
  }

  if (isLoading) {
    return <div className="text-muted-foreground text-sm">Loading policies…</div>;
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold">Retention Policies</h2>
        <button
          onClick={handleSave}
          disabled={edits.size === 0 || updateMutation.isPending}
          className="rounded bg-primary px-3 py-1 text-sm text-primary-foreground disabled:opacity-50"
        >
          {updateMutation.isPending ? "Saving…" : "Save"}
        </button>
      </div>
      {policies.length === 0 ? (
        <p className="text-muted-foreground text-sm italic">
          No retention policies found.
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b text-left text-xs text-muted-foreground">
                <th className="pb-2 pr-4">Kind</th>
                <th className="pb-2 pr-4">TTL (days)</th>
                <th className="pb-2 pr-4">Max rows</th>
                <th className="pb-2 text-right">Updated</th>
              </tr>
            </thead>
            <tbody>
              {policies.map((p) => (
                <RetentionPolicyRow
                  key={p.kind}
                  policy={p}
                  onChange={handleChange}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
      {updateMutation.isError && (
        <p className="text-destructive text-sm">
          Failed to save policies. Please try again.
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Compaction log feed (§10.4 §3)
// ---------------------------------------------------------------------------

function CompactionLogSection() {
  const { data: logResp, isLoading } = useMemoryCompactionLog(50);
  const entries = logResp?.data ?? [];

  if (isLoading) {
    return <div className="text-muted-foreground text-sm">Loading compaction log…</div>;
  }

  return (
    <div className="space-y-3">
      <h2 className="text-lg font-semibold">Compaction Log</h2>
      {entries.length === 0 ? (
        <p className="text-muted-foreground text-sm italic">No compaction events recorded.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b text-left text-xs text-muted-foreground">
                <th className="pb-2 pr-4">Time</th>
                <th className="pb-2 pr-4">Kind</th>
                <th className="pb-2 pr-4">Rows removed</th>
                <th className="pb-2 text-right">Bytes freed</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((e) => (
                <tr key={e.id} className="border-b">
                  <td className="py-1.5 pr-4 text-xs text-muted-foreground">
                    {new Date(e.ts).toLocaleString()}
                  </td>
                  <td className="py-1.5 pr-4 font-mono">{e.kind}</td>
                  <td className="py-1.5 pr-4">{e.rows_removed.toLocaleString()}</td>
                  <td className="py-1.5 text-right text-muted-foreground">
                    {e.bytes_freed != null ? e.bytes_freed.toLocaleString() : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// MemoryPage
// ---------------------------------------------------------------------------

export default function MemoryPage() {
  return (
    <div className="space-y-6">
      {/* Overture (Bands 1 & 2): headline, Voice, KPI strip, pipeline band.
          Answers "is remembering working" before any scrolling. */}
      <MemoryOverture />

      {/* §10.4 §1: Tier flow (events → mid → long with counts) */}
      <MemoryTierCards />

      {/* Band 3 — Registers + rail. grid 1.4fr/1fr, gap 56px:
          left = the one search affordance + focused register (or results);
          right = the attention rail (the page's state color) + recent activity.
          The old Inspect section and the carded activity timeline are gone. */}
      <div className="grid gap-x-14 gap-y-10 lg:grid-cols-[1.4fr_1fr]">
        <MemoryBrowser />
        <AttentionRail />
      </div>

      {/* Band 4 — Housekeeping. The stale-embeddings rail action anchors here. */}
      <div id="housekeeping" className="space-y-6 scroll-mt-6">
        {/* §10.4 §2: Retention policy editable table */}
        <RetentionPoliciesSection />

        {/* §10.4 §3: Compaction log feed */}
        <CompactionLogSection />

        {/* Embedding migration panel (bu-9bqsy) */}
        <ReembedPanel />
      </div>
    </div>
  );
}
