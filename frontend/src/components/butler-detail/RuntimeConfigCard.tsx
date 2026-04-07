import { useState } from "react";

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

// Known core groups for the multi-select editor.
const KNOWN_CORE_GROUPS = [
  "infra",
  "state",
  "scheduling",
  "sessions",
  "notifications",
  "media",
  "temporal",
  "module_mgmt",
  "switchboard_routing",
  "switchboard_backfill",
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
      // Error handled by mutation state
    }
  };

  const updateField = (field: keyof RuntimeConfigPatch, value: unknown) => {
    setEditState((prev) => ({ ...prev, [field]: value }));
    setRestartFields([]);
  };

  const currentModel = editState.model ?? config.model ?? "";
  const currentRuntimeType = editState.runtime_type ?? config.runtime_type;
  const currentSessionTimeout = editState.session_timeout_s ?? config.session_timeout_s;
  const currentMaxConcurrent = editState.max_concurrent ?? config.max_concurrent;
  const currentMaxQueued = editState.max_queued ?? config.max_queued;
  const currentCoreGroups = editState.core_groups ?? config.core_groups ?? [];

  const hasChanges = Object.keys(editState).length > 0;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Runtime Config</CardTitle>
        <CardAction>
          {config.updated_at && (
            <span className="text-xs text-muted-foreground mr-3">
              Updated: {new Date(config.updated_at).toLocaleString()}
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
          <div className="rounded-md bg-amber-50 dark:bg-amber-950 border border-amber-200 dark:border-amber-800 p-3 text-sm">
            Restart required for: {restartFields.join(", ")}
          </div>
        )}

        {patchMutation.isError && (
          <div className="rounded-md bg-destructive/10 p-3 text-sm text-destructive">
            Save failed: {patchMutation.error instanceof Error ? patchMutation.error.message : "Unknown error"}
          </div>
        )}

        {/* Hot fields */}
        <div className="grid grid-cols-2 gap-4">
          <div>
            <Label className="text-xs">
              Model <FieldTierBadge tier={tiers?.model ?? "hot"} />
            </Label>
            <Input
              value={currentModel}
              onChange={(e) => updateField("model", e.target.value || null)}
              placeholder="(use catalog default)"
              className="mt-1"
            />
          </div>
          <div>
            <Label className="text-xs">
              Runtime Type <FieldTierBadge tier={tiers?.runtime_type ?? "hot"} />
            </Label>
            <Input
              value={currentRuntimeType}
              onChange={(e) => updateField("runtime_type", e.target.value)}
              className="mt-1"
            />
          </div>
        </div>

        <div className="grid grid-cols-2 gap-4">
          <div>
            <Label className="text-xs">
              Session Timeout (s) <FieldTierBadge tier={tiers?.session_timeout_s ?? "hot"} />
            </Label>
            <Input
              type="number"
              value={currentSessionTimeout}
              onChange={(e) => updateField("session_timeout_s", parseInt(e.target.value) || 900)}
              className="mt-1"
            />
          </div>
        </div>

        {/* Cold fields */}
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
