/** Pointer-drag types and helpers for creating, moving and resizing grid events. */

import { format } from "date-fns";

import type { UnifiedCalendarEntry } from "@/api/types.ts";
import { MINUTES_PER_DAY } from "@/lib/calendar-grid";
import { resolveButlerEventTarget } from "@/pages/calendar/workspace-model";

/** Pixel movement past which a pointer gesture counts as a drag (not a click). */
export const DRAG_THRESHOLD_PX = 4;

/** Format a minute-of-day value as an `HH:mm` label. */
export function formatMinuteLabel(minutes: number): string {
  const clamped = Math.min(MINUTES_PER_DAY, Math.max(0, minutes));
  if (clamped === MINUTES_PER_DAY) return "24:00";
  return format(
    new Date(2000, 0, 1, Math.floor(clamped / 60), clamped % 60),
    "HH:mm",
  );
}

/** Whether an entry carries an RRULE (i.e. is a recurring occurrence). */
export function isRecurringInstance(entry: UnifiedCalendarEntry): boolean {
  return typeof entry.rrule === "string" && entry.rrule.trim().length > 0;
}

/** Whether a grid entry can be dragged to move/resize via existing update endpoints. */
export function isGridDraggable(entry: UnifiedCalendarEntry): boolean {
  if (entry.all_day) {
    return false;
  }
  if (entry.source_type === "provider_event") {
    // Recurring provider occurrences are draggable; the drop is routed through the
    // recurrence scope sheet (this / following / series) instead of committing directly.
    return !!entry.provider_event_id && entry.editable;
  }
  // Butler-lane events (scheduled tasks / reminders) update via
  // calendar_update_butler_event, which has no scope-aware path yet, so keep
  // recurring butler instances non-draggable.
  if (isRecurringInstance(entry)) {
    return false;
  }
  return resolveButlerEventTarget(entry) !== null && !!entry.butler_name;
}

/** Immutable origin of an in-flight grid drag gesture. */
export type DragOrigin =
  | {
      mode: "create";
      pointerId: number;
      downX: number;
      downY: number;
      dayIndex: number;
      anchorMin: number;
      moved: boolean;
    }
  | {
      mode: "move";
      pointerId: number;
      downX: number;
      downY: number;
      entry: UnifiedCalendarEntry;
      originStartMin: number;
      durationMin: number;
      grabOffsetMin: number;
      moved: boolean;
    }
  | {
      mode: "resize";
      pointerId: number;
      downX: number;
      downY: number;
      entry: UnifiedCalendarEntry;
      dayIndex: number;
      startMin: number;
      moved: boolean;
    };

/** Live preview of the current drag, rendered as a translucent block. */
export type GridDragPreview =
  | { mode: "create"; dayIndex: number; startMin: number; endMin: number }
  | {
      mode: "move";
      dayIndex: number;
      startMin: number;
      endMin: number;
      entryId: string;
    }
  | {
      mode: "resize";
      dayIndex: number;
      startMin: number;
      endMin: number;
      entryId: string;
    };

/** Ghost left at an event's prior slot after a successful move, enabling one-click undo. */
export interface MovedGhost {
  entry: UnifiedCalendarEntry;
  prevStartIso: string;
  prevEndIso: string;
  nextStartIso: string;
  nextEndIso: string;
}
