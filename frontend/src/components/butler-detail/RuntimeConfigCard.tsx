import { useState } from "react";
import { Time } from "@/components/ui/time";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardAction,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { usePatchRuntimeConfig, useRuntimeConfig } from "@/hooks/use-butlers";
import type { RuntimeConfigPatch } from "@/api/index.ts";

// Tool exposure policy choices for the single-select editor.
const TOOL_EXPOSURE_POLICIES = [
  { value: "eager_filtered", label: "Eager filtered" },
  { value: "auto", label: "Automatic verified discovery" },
] as const;

// Known core groups for the multi-select editor.
const KNOWN_CORE_GROUPS = [
  "infra",
  "state",
  "scheduling",
  "sessions",
  "notifications",
  "media",
  "graph",
  "temporal",
  "module_mgmt",
  "switchboard_routing",
  "switchboard_backfill",
  "delegation",
  "domain_events",
  "fleet_cases",
] as const;

interface RuntimeConfigCardProps {
  butlerName: string;
}

function FieldTierBadge({ tier }: { tier: "hot" | "cold" }) {
  return (
    <Badge variant={tier === "hot" ? "default" : "secondary"} className="ml-2 text-[10px]">
      {tier === "hot" ? "hot" : "restart required"}
    </Badge>
  );
}

export default function RuntimeConfigCard({ butlerName }: RuntimeConfigCardProps) {
  const { data, isLoading, isError, error } = useRuntimeConfig(butlerName);
  const patchMutation = usePatchRuntimeConfig(butlerName);
  const [editState, setEditState] = useState<RuntimeConfigPatch>({});
  const [restartFields, setRestartFields] = useState<string[]>([]);

  if (isLoading) {
    return (
      <Card>
        <CardHeader><CardTitle>Runtime Config</CardTitle></CardHeader>
        <CardContent><p className="text-sm text-muted-foreground">Loading...</p></CardContent>
      </Card>
    );
  }

  if (isError) {
    return (
      <Card>
        <CardHeader><CardTitle>Runtime Config</CardTitle></CardHeader>
        <CardContent>
          <p className="text-sm text-destructive">
            {error instanceof Error ? error.message : "Failed to load"}
          </p>
        </CardContent>
      </Card>
    );
  }

  if (!data) return null;

  const config = data;
  const tiers = config.field_tiers;

  const handleSave = async () => {
    if (Object.keys(editState).length === 0) return;
    try {
      const result = await patchMutation.mutateAsync(editState);
      setRestartFields(result.restart_required);
      setEditState({});
    } catch {
      // Failed save: revert the exposure-policy edit so the card never
      // presents an unconfirmed value as the effective policy. Other
      // pending edits are left for the user to retry.
      setEditState((prev) => {
        if (!("tool_exposure_policy" in prev)) return prev;
        const next = { ...prev };
        delete next.tool_exposure_policy;
        return next;
      });
    }
  };

  const updateField = (field: keyof RuntimeConfigPatch, value: unknown) => {
    setEditState((prev) => ({ ...prev, [field]: value }));
    setRestartFields([]);
  };

  const currentMaxConcurrent = editState.max_concurrent ?? config.max_concurrent;
  const currentMaxQueued = editState.max_queued ?? config.max_queued;
  const currentCoreGroups = editState.core_groups ?? config.core_groups ?? [];
  const currentToolExposurePolicy =
    editState.tool_exposure_policy ?? config.tool_exposure_policy ?? "eager_filtered";

  const hasChanges = Object.keys(editState).length > 0;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Runtime Config</CardTitle>
        <CardAction>
          {config.updated_at && (
            <span className="text-xs text-muted-foreground mr-3">
              Updated: <Time value={config.updated_at} mode="absolute" />
            </span>
          )}
          <Button
            size="xs"
            disabled={!hasChanges || patchMutation.isPending}
            onClick={handleSave}
          >
            {patchMutation.isPending ? "Saving..." : "Save"}
          </Button>
        </CardAction>
      </CardHeader>
      <CardContent className="space-y-4">
        {restartFields.length > 0 && (
          <div className="rounded-md bg-[var(--amber)]/10 border border-[var(--amber)]/40 p-3 text-sm">
            Restart required for: {restartFields.join(", ")}
          </div>
        )}

        {patchMutation.isError && (
          <div className="rounded-md bg-destructive/10 p-3 text-sm text-destructive">
            Save failed: {patchMutation.error instanceof Error ? patchMutation.error.message : "Unknown error"}
          </div>
        )}

        {/* Operational fields */}
        <div className="grid grid-cols-2 gap-4">
          <div>
            <Label className="text-xs">
              Max Concurrent <FieldTierBadge tier={tiers?.max_concurrent ?? "cold"} />
            </Label>
            <Input
              type="number"
              value={currentMaxConcurrent}
              onChange={(e) => updateField("max_concurrent", parseInt(e.target.value) || 3)}
              className="mt-1"
            />
          </div>
          <div>
            <Label className="text-xs">
              Max Queued <FieldTierBadge tier={tiers?.max_queued ?? "cold"} />
            </Label>
            <Input
              type="number"
              value={currentMaxQueued}
              onChange={(e) => updateField("max_queued", parseInt(e.target.value) || 10)}
              className="mt-1"
            />
          </div>
        </div>

        {/* Tool Exposure Policy single-select */}
        <div>
          <Label className="text-xs">
            Tool Exposure Policy <FieldTierBadge tier={tiers?.tool_exposure_policy ?? "hot"} />
          </Label>
          <div className="mt-2 flex flex-wrap gap-2">
            {TOOL_EXPOSURE_POLICIES.map(({ value, label }) => (
              <Badge
                key={value}
                variant={currentToolExposurePolicy === value ? "default" : "outline"}
                className="cursor-pointer select-none"
                onClick={() => updateField("tool_exposure_policy", value)}
              >
                {label}
              </Badge>
            ))}
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            Applies to newly planned sessions, no daemon restart needed.
          </p>
          <p className="mt-1 text-xs text-muted-foreground">
            Automatic verified discovery uses native tool search only when verified for the resolved runtime and model, falling back to a separately verified eager profile when one is available; otherwise the session is unavailable rather than guaranteed native discovery.
          </p>
        </div>

        {/* Core Groups multi-select */}
        <div>
          <Label className="text-xs">
            Core Groups <FieldTierBadge tier={tiers?.core_groups ?? "cold"} />
          </Label>
          <div className="mt-2 flex flex-wrap gap-2">
            {KNOWN_CORE_GROUPS.map((group) => {
              const active = currentCoreGroups.includes(group);
              return (
                <Badge
                  key={group}
                  variant={active ? "default" : "outline"}
                  className="cursor-pointer select-none"
                  onClick={() => {
                    const next = active
                      ? currentCoreGroups.filter((g) => g !== group)
                      : [...currentCoreGroups, group];
                    updateField("core_groups", next.length > 0 ? next : null);
                  }}
                >
                  {group}
                </Badge>
              );
            })}
          </div>
          {config.core_groups === null && !editState.core_groups && (
            <p className="mt-1 text-xs text-muted-foreground">
              All groups enabled (no filter set)
            </p>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
