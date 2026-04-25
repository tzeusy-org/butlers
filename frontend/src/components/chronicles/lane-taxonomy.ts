// ---------------------------------------------------------------------------
// Chronicles Lane Taxonomy — bu-ig72b.5
//
// Source of truth for the visual presentation of each chronicle category.
// Backend (aggregations.py) owns the category string definitions; this file
// maps those strings to display labels, colours, icons, and sort order.
//
// Backend never returns colours, labels, or icons — those live here only.
// ---------------------------------------------------------------------------

import type { LucideIcon } from "lucide-react"
import {
  Briefcase,
  Calendar,
  CircleQuestionMark,
  Gamepad2,
  House,
  Moon,
  Plane,
  Music,
  Utensils,
} from "lucide-react"

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** All stable category strings emitted by the chronicler backend. */
export type Category =
  | "work"
  | "calendar"
  | "music"
  | "gaming"
  | "travel"
  | "sleep"
  | "meal"
  | "home"
  | "other"

/** Visual configuration for a single Gantt lane / pie slice. */
export interface LaneConfig {
  /** Human-readable label shown in the UI. */
  label: string
  /**
   * Tailwind utility class(es) for the lane's accent colour.
   * Use the `bg-*` form — consumers may derive `text-*`/`border-*` variants
   * from this class or compose it with opacity modifiers.
   */
  colour: string
  /** Lucide-react icon component associated with this category. */
  icon: LucideIcon
  /**
   * Ascending sort position for rendering lanes in a predictable order.
   * Lower numbers appear first.
   */
  sortOrder: number
}

// ---------------------------------------------------------------------------
// Taxonomy constant
// ---------------------------------------------------------------------------

/**
 * Maps each stable category string → visual presentation config.
 *
 * Ordering rationale: work first (most frequently populated), then calendar,
 * then recreational (music, gaming, travel), then biological (sleep, meal),
 * then home, then catch-all other last.
 */
export const LANE_TAXONOMY: Readonly<Record<Category, LaneConfig>> = {
  work: {
    label: "Work",
    colour: "bg-blue-600",
    icon: Briefcase,
    sortOrder: 0,
  },
  calendar: {
    label: "Calendar",
    colour: "bg-indigo-500",
    icon: Calendar,
    sortOrder: 1,
  },
  music: {
    label: "Music",
    colour: "bg-purple-500",
    icon: Music,
    sortOrder: 2,
  },
  gaming: {
    label: "Gaming",
    colour: "bg-violet-600",
    icon: Gamepad2,
    sortOrder: 3,
  },
  travel: {
    label: "Travel",
    colour: "bg-cyan-500",
    icon: Plane,
    sortOrder: 4,
  },
  sleep: {
    label: "Sleep",
    colour: "bg-slate-500",
    icon: Moon,
    sortOrder: 5,
  },
  meal: {
    label: "Meal",
    colour: "bg-amber-500",
    icon: Utensils,
    sortOrder: 6,
  },
  home: {
    label: "Home",
    colour: "bg-emerald-600",
    icon: House,
    sortOrder: 7,
  },
  other: {
    label: "Other",
    colour: "bg-slate-400",
    icon: CircleQuestionMark,
    sortOrder: 8,
  },
}
