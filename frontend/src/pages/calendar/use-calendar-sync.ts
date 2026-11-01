/**
 * Calendar source sync: the shared sync mutation plus the "sync all" and
 * per-source (optionally full recovery) handlers with their toasts.
 */

import { useState } from "react";
import { toast } from "sonner";

import type { CalendarWorkspaceSourceFreshness } from "@/api/types.ts";
import { useSyncCalendarWorkspace } from "@/hooks/use-calendar-workspace";
import { sourceName } from "@/pages/calendar/workspace-model";

export function useCalendarSync() {
  const syncMutation = useSyncCalendarWorkspace();
  const [syncingSourceKey, setSyncingSourceKey] = useState<string | null>(null);

  async function handleSyncAll() {
    try {
      const result = await syncMutation.mutateAsync({ all: true });
      toast.success(
        `Sync queued for ${result.data.triggered_count} calendar owner(s).`,
      );
    } catch (error) {
      toast.error(
        error instanceof Error ? error.message : "Failed to trigger sync.",
      );
    }
  }

  async function handleSyncSource(
    source: CalendarWorkspaceSourceFreshness,
    { full = false }: { full?: boolean } = {},
  ) {
    setSyncingSourceKey(source.source_key);
    try {
      const result = await syncMutation.mutateAsync({
        source_key: source.source_key,
        butler: source.butler_name || undefined,
        full,
      });
      const target = result.data.targets[0];
      if (target?.status === "failed") {
        toast.error(target.error || "Source sync failed.");
      } else if (full) {
        toast.success(
          target?.coalesced
            ? `Recovery sync joined queued work for ${sourceName(source)}.`
            : `Recovery sync queued for ${sourceName(source)}.`,
        );
      } else {
        toast.success(
          target?.coalesced
            ? `Sync joined queued work for ${sourceName(source)}.`
            : `Sync queued for ${sourceName(source)}.`,
        );
      }
    } catch (error) {
      toast.error(
        error instanceof Error
          ? error.message
          : "Failed to trigger source sync.",
      );
    } finally {
      setSyncingSourceKey(null);
    }
  }

  const syncButtonLabel = syncMutation.isPending ? "Syncing..." : "Sync now";

  return { syncMutation, syncingSourceKey, handleSyncAll, handleSyncSource, syncButtonLabel };
}

export type CalendarSync = ReturnType<typeof useCalendarSync>;
