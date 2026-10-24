/**
 * Command-palette verbs and keyboard shortcuts for the calendar page
 * (bu-t64p2): jump to today, step the range, search, and create a butler event.
 */

import { useEffect, useMemo, useRef } from "react";

import type { CalendarWorkspaceView } from "@/api/types.ts";
import { useRegisterShortcut, type ShortcutBinding } from "@/hooks/use-register-shortcut";
import { useRegisterCommands, type PaletteCommand } from "@/lib/command-registry";
import type { UpdateCalendarQuery } from "@/pages/calendar/use-calendar-url-state";
import { shiftAnchor, type CalendarRange } from "@/pages/calendar/workspace-model";

interface CalendarCommandsArgs {
  view: CalendarWorkspaceView;
  range: CalendarRange;
  anchor: Date;
  updateQuery: UpdateCalendarQuery;
  openButlerCreateDialog: () => void;
  openSearch: () => void;
}

export function useCalendarCommands({
  view,
  range,
  anchor,
  updateQuery,
  openButlerCreateDialog,
  openSearch,
}: CalendarCommandsArgs) {
  // Only butler events are created from the dashboard; provider events are
  // created by the quick-add bar, which has its own input.
  const canCreateEvent = view === "butler";

  // The callbacks and anchor change every render, but the memos below only
  // recompute when canCreateEvent changes. Route every invocation through a ref
  // so a shortcut pressed after navigating always sees the latest closure
  // (gemini-code-assist, PR #2958).
  const actionsRef = useRef({ openButlerCreateDialog, openSearch, updateQuery, anchor, range });
  useEffect(() => {
    actionsRef.current = { openButlerCreateDialog, openSearch, updateQuery, anchor, range };
  });

  // The handlers below only read the ref when invoked (key press / palette
  // pick), never during render; the React-compiler ref rule cannot see that
  // through the memo-stable closures.
  /* eslint-disable react-hooks/refs */
  const goToday = () => actionsRef.current.updateQuery({ anchor: new Date() });
  const step = (direction: 1 | -1) => () => {
    const { anchor: currentAnchor, range: currentRange } = actionsRef.current;
    actionsRef.current.updateQuery({
      anchor: shiftAnchor(currentAnchor, currentRange, direction),
    });
  };

  const commands = useMemo<PaletteCommand[]>(() => {
    const list: PaletteCommand[] = [];
    if (canCreateEvent) {
      list.push({
        id: "calendar-new-event",
        label: "Create butler event",
        keywords: ["new", "event", "create"],
        perform: () => actionsRef.current.openButlerCreateDialog(),
        binding: ["c"],
      });
    }
    list.push(
      {
        id: "calendar-jump-today",
        label: "Jump to today",
        keywords: ["today", "anchor"],
        perform: goToday,
        binding: ["t"],
      },
      {
        id: "calendar-previous-range",
        label: "Previous range",
        keywords: ["calendar", "previous", "range"],
        perform: step(-1),
        binding: ["←"],
      },
      {
        id: "calendar-next-range",
        label: "Next range",
        keywords: ["calendar", "next", "range"],
        perform: step(1),
        binding: ["→"],
      },
      {
        id: "calendar-search-events",
        label: "Search events",
        keywords: ["find", "search"],
        perform: () => actionsRef.current.openSearch(),
      },
    );
    return list;
    // goToday/step only read the ref, so they are stable by construction.
  }, [canCreateEvent]);
  useRegisterCommands(commands);

  const shortcuts = useMemo<ShortcutBinding[]>(() => {
    const bindings: ShortcutBinding[] = [
      { key: "t", display: ["t"], description: "Jump to today", handler: goToday },
      { key: "ArrowLeft", display: ["←"], description: "Previous range", handler: step(-1) },
      { key: "ArrowRight", display: ["→"], description: "Next range", handler: step(1) },
    ];
    if (canCreateEvent) {
      bindings.push({
        key: "c",
        display: ["c"],
        description: "Create butler event",
        handler: () => actionsRef.current.openButlerCreateDialog(),
      });
    }
    return bindings;
  }, [canCreateEvent]);
  useRegisterShortcut(shortcuts);
/* eslint-enable react-hooks/refs */
}
