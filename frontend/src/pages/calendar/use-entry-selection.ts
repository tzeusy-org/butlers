/**
 * Entry selection for the calendar page: the docked detail panel, opening an
 * invitation by id, the search palette's jump-and-flash, and day-briefing links.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { getCalendarWorkspaceEntry } from "@/api/index.ts";
import type { UnifiedCalendarEntry } from "@/api/types.ts";
import type { UpdateCalendarQuery } from "@/pages/calendar/use-calendar-url-state";

interface EntrySelectionArgs {
  entries: UnifiedCalendarEntry[];
  workspaceFetching: boolean;
  updateQuery: UpdateCalendarQuery;
  /** `?entry=<id>` deep link (the day sheet's door): opened once, then cleared. */
  entryParam?: string | null;
}

export function useEntrySelection({
  entries,
  workspaceFetching,
  updateQuery,
  entryParam = null,
}: EntrySelectionArgs) {
  const queryClient = useQueryClient();
  const [selectedEntry, setSelectedEntry] = useState<UnifiedCalendarEntry | null>(null);
  const [invitationDetailLoading, setInvitationDetailLoading] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [flashedEntryId, setFlashedEntryId] = useState<string | null>(null);

  async function openInvitation(entryId: string): Promise<UnifiedCalendarEntry | null> {
    setInvitationDetailLoading(true);
    try {
      const response = await queryClient.fetchQuery({
        queryKey: ["calendar-workspace-entry", entryId],
        queryFn: () => getCalendarWorkspaceEntry(entryId),
      });
      if (response.data.entry_id !== entryId) throw new Error("Entry identity mismatch");
      setSelectedEntry(response.data);
      return response.data;
    } catch {
      toast.error("Invitation details unavailable. Try again shortly.");
      return null;
    } finally {
      setInvitationDetailLoading(false);
    }
  }

  // Open the entry named by ``?entry=``: drop the param at once (so closing the panel does not
  // reopen it), fetch the entry, open its panel and move the grid to its day. The latest
  // ``updateQuery`` is read through a ref so the async tail never replays a stale URL.
  const updateRef = useRef(updateQuery);
  updateRef.current = updateQuery;
  // Not a cleanup-cancel flag: dropping the param re-runs the effect, which must not cancel the
  // fetch it just started. Only leaving the page should stop the navigation that follows it.
  const mountedRef = useRef(true);
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);
  useEffect(() => {
    if (!entryParam) return;
    updateRef.current({ entry: null });
    void openInvitation(entryParam).then((entry) => {
      if (entry && mountedRef.current) {
        updateRef.current({
          view: entry.view === "butler" ? "butler" : "user",
          range: "day",
          anchor: new Date(entry.start_at),
        });
      }
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps -- keyed on the param alone
  }, [entryParam]);

  // Jump from a search match: navigate the grid to the match's day, then flash it.
  const handleSearchJump = useCallback(
    (entry: UnifiedCalendarEntry) => {
      setSearchOpen(false);
      updateQuery({
        view: entry.view === "butler" ? "butler" : "user",
        range: "day",
        anchor: new Date(entry.start_at),
      });
      setFlashedEntryId(entry.entry_id);
    },
    [updateQuery],
  );

  // Link a day-briefing chip to its underlying item: navigate the grid to the
  // overlay entry's day. Overlay entries are read-only domain context (synthetic
  // ids), so the link is to the day they live on, not a detail panel.
  const handleBriefingSelect = useCallback(
    (entry: UnifiedCalendarEntry) => {
      updateQuery({ range: "day", anchor: new Date(entry.start_at) });
    },
    [updateQuery],
  );

  // Once the grid has the flashed entry rendered, scroll it into view and pulse it.
  useEffect(() => {
    if (!flashedEntryId || workspaceFetching) return;
    const el = document.querySelector<HTMLElement>(
      `[data-calendar-entry-id="${CSS.escape(flashedEntryId)}"]`,
    );
    if (!el) return;
    el.scrollIntoView({ behavior: "smooth", block: "center" });
    el.classList.add("calendar-entry-flash");
    const timer = window.setTimeout(() => {
      el.classList.remove("calendar-entry-flash");
      setFlashedEntryId(null);
    }, 2200);
    return () => {
      window.clearTimeout(timer);
      el.classList.remove("calendar-entry-flash");
    };
  }, [flashedEntryId, entries, workspaceFetching]);

  return {
    selectedEntry,
    openDetailPanel: setSelectedEntry,
    closeDetailPanel: () => setSelectedEntry(null),
    openInvitation,
    invitationDetailLoading,
    searchOpen,
    setSearchOpen,
    handleSearchJump,
    handleBriefingSelect,
  };
}
