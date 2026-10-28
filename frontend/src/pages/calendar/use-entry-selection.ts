/**
 * Entry selection for the calendar page: the docked detail panel, opening an
 * invitation by id, the search palette's jump-and-flash, and day-briefing links.
 */

import { useCallback, useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { getCalendarWorkspaceEntry } from "@/api/client.ts";
import type { UnifiedCalendarEntry } from "@/api/types.ts";
import type { UpdateCalendarQuery } from "@/pages/calendar/use-calendar-url-state";

interface EntrySelectionArgs {
  entries: UnifiedCalendarEntry[];
  workspaceFetching: boolean;
  updateQuery: UpdateCalendarQuery;
}

export function useEntrySelection({ entries, workspaceFetching, updateQuery }: EntrySelectionArgs) {
  const queryClient = useQueryClient();
  const [selectedEntry, setSelectedEntry] = useState<UnifiedCalendarEntry | null>(null);
  const [invitationDetailLoading, setInvitationDetailLoading] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [flashedEntryId, setFlashedEntryId] = useState<string | null>(null);

  async function openInvitation(entryId: string) {
    setInvitationDetailLoading(true);
    try {
      const response = await queryClient.fetchQuery({
        queryKey: ["calendar-workspace-entry", entryId],
        queryFn: () => getCalendarWorkspaceEntry(entryId),
      });
      if (response.data.entry_id !== entryId) throw new Error("Entry identity mismatch");
      setSelectedEntry(response.data);
    } catch {
      toast.error("Invitation details unavailable. Try again shortly.");
    } finally {
      setInvitationDetailLoading(false);
    }
  }

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
