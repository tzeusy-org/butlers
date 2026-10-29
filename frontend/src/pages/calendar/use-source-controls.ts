/**
 * Source enable/hide state for the calendar workspace. Enabling is the
 * persisted sync toggle (mirrored optimistically); hiding is a view-only
 * preference that removes a source from the grid query.
 */

import { useEffect, useState } from "react";
import { toast } from "sonner";

import type { CalendarWorkspaceSourceFreshness } from "@/api/types.ts";
import { useCalendarAccounts, useToggleCalendarSource } from "@/hooks/use-calendar-workspace";

export function useSourceControls(connectedSources: CalendarWorkspaceSourceFreshness[]) {
  // The backend is the source of truth for whether a calendar is enabled as a
  // sync source (``sync_enabled`` on each connected source). We mirror that into
  // a local Set for snappy optimistic toggling; it is re-seeded whenever meta
  // refreshes.
  const accountsQuery = useCalendarAccounts();
  const toggleSourceMutation = useToggleCalendarSource();
  const [disabledSources, setDisabledSources] = useState<Set<string>>(
    new Set<string>(),
  );
  // View-only hide/show set. This is a purely client-side concern: hiding a
  // source removes its events from the grid WITHOUT touching the persisted
  // ``sync_enabled`` sync toggle, so a still-syncing calendar can be hidden and
  // a sync-disabled calendar's prior events stay visible. Never persisted to
  // the server; resets on reload by design.
  const [hiddenSources, setHiddenSources] = useState<Set<string>>(
    new Set<string>(),
  );

  // Re-seed the optimistic mirror from the server list; this is the documented
  // "external system -> local state" sync, so the setState-in-effect is intended.
  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    if (toggleSourceMutation.isPending) return;
    const serverDisabled = connectedSources
      .filter((source) => source.sync_enabled === false)
      .map((source) => source.source_key);
    setDisabledSources(new Set(serverDisabled));
  }, [connectedSources, toggleSourceMutation.isPending]);
  /* eslint-enable react-hooks/set-state-in-effect */

  function toggleSourceHidden(source: CalendarWorkspaceSourceFreshness) {
    const sourceKey = source.source_key;
    setHiddenSources((prev) => {
      const next = new Set(prev);
      if (next.has(sourceKey)) next.delete(sourceKey);
      else next.add(sourceKey);
      return next;
    });
  }

  function toggleSourceEnabled(source: CalendarWorkspaceSourceFreshness) {
    const sourceKey = source.source_key;
    const willEnable = disabledSources.has(sourceKey);
    // Optimistically reflect the new state.
    setDisabledSources((prev) => {
      const next = new Set(prev);
      if (willEnable) next.delete(sourceKey);
      else next.add(sourceKey);
      return next;
    });
    if (!source.butler_name) {
      toast.error("Cannot toggle this source: no owning butler resolved");
      return;
    }
    toggleSourceMutation.mutate(
      {
        butler: source.butler_name,
        source_key: sourceKey,
        enabled: willEnable,
      },
      {
        onError: (err) => {
          // Revert the optimistic update on failure.
          setDisabledSources((prev) => {
            const next = new Set(prev);
            if (willEnable) next.add(sourceKey);
            else next.delete(sourceKey);
            return next;
          });
          toast.error(`Failed to update source: ${err.message}`);
        },
        onSuccess: () => {
          toast.success(
            willEnable ? "Source enabled for sync" : "Source disabled",
          );
        },
      },
    );
  }

  return {
    accountsQuery,
    disabledSources,
    hiddenSources,
    toggleSourceEnabled,
    toggleSourceHidden,
  };
}

export type SourceControls = ReturnType<typeof useSourceControls>;
