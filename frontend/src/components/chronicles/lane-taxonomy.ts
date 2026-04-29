// ---------------------------------------------------------------------------
// Chronicles Lane Taxonomy — bu-ig72b.5 / bu-jomz2
//
// Source of truth for the visual presentation of each chronicle category.
// Backend (aggregations.py) owns the category string definitions; this file
// maps those strings to display labels, colours, icons, and sort order.
//
// Backend never returns colours, labels, or icons — those live here only.
//
// core.sessions episodes are split into two lanes by trigger_source:
//   "conversations" — trigger_source='route'  (user→butler interactions)
//   "tasks"         — all other trigger_source values  (scheduled/daemon work)
// ---------------------------------------------------------------------------

import type { LucideIcon } from "lucide-react"
import {
  Calendar,
  CircleQuestionMark,
  Gamepad2,
  House,
  MessageCircle,
  Moon,
  Plane,
  Music,
  Terminal,
  Utensils,
} from "lucide-react"

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** All stable category strings emitted by the chronicler backend. */
export type Category =
  | "conversations"
  | "tasks"
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
  /**
   * Hex colour value for consumers that cannot use Tailwind classes directly
   * (e.g. recharts SVG fill attributes).
   * Must visually match `colour` above.
   */
  hex: string
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
 * Ordering rationale: conversations first (most frequent user-visible lane),
 * then tasks (scheduled/daemon work), then calendar, then recreational
 * (music, gaming, travel), then biological (sleep, meal), then home,
 * then catch-all other last.
 */
export const LANE_TAXONOMY: Readonly<Record<Category, LaneConfig>> = {
  conversations: {
    label: "Conversations",
    colour: "bg-blue-600",
    hex: "#2563eb",
    icon: MessageCircle,
    sortOrder: 0,
  },
  tasks: {
    label: "Tasks",
    colour: "bg-sky-500",
    hex: "#0ea5e9",
    icon: Terminal,
    sortOrder: 1,
  },
  calendar: {
    label: "Calendar",
    colour: "bg-indigo-500",
    hex: "#6366f1",
    icon: Calendar,
    sortOrder: 2,
  },
  music: {
    label: "Music",
    colour: "bg-purple-500",
    hex: "#a855f7",
    icon: Music,
    sortOrder: 3,
  },
  gaming: {
    label: "Gaming",
    colour: "bg-violet-600",
    hex: "#7c3aed",
    icon: Gamepad2,
    sortOrder: 4,
  },
  travel: {
    label: "Travel",
    colour: "bg-cyan-500",
    hex: "#06b6d4",
    icon: Plane,
    sortOrder: 5,
  },
  sleep: {
    label: "Sleep",
    colour: "bg-slate-500",
    hex: "#64748b",
    icon: Moon,
    sortOrder: 6,
  },
  meal: {
    label: "Meal",
    colour: "bg-amber-500",
    hex: "#f59e0b",
    icon: Utensils,
    sortOrder: 7,
  },
  home: {
    label: "Home",
    colour: "bg-emerald-600",
    hex: "#059669",
    icon: House,
    sortOrder: 8,
  },
  other: {
    label: "Other",
    colour: "bg-slate-400",
    hex: "#94a3b8",
    icon: CircleQuestionMark,
    sortOrder: 9,
  },
}

// ---------------------------------------------------------------------------
// (source_name, episode_type) → Category mapping
//
// Mirrors `_CATEGORY_MAP` in `src/butlers/chronicler/aggregations.py`. Used as
// a frontend fallback when the backend has not yet attached a `category` field
// to the episode response (bug 1 fix). The backend remains the source of truth
// — keep this table in sync with `_CATEGORY_MAP` whenever new sources land.
//
// For core.sessions episodes the backend dispatches by trigger_source;
// this fallback table cannot resolve that, so it maps to "tasks" (the default
// for unknown / NULL trigger_source). Callers should prefer the backend-supplied
// `category` field for core.sessions episodes whenever available.
// ---------------------------------------------------------------------------

const SOURCE_CATEGORY_MAP: Record<string, Category> = {
  "core.sessions|work": "tasks",
  "google_calendar.completed|scheduled_block": "calendar",
  "spotify.session_summary|listening_episode": "music",
  "steam.play_history|play_episode": "gaming",
  "owntracks.points|movement_episode": "travel",
  "google_health.measurements|sleep_episode": "sleep",
  "health.meals|eating_event": "meal",
  "home_assistant.history|presence_episode": "home",
}

/**
 * Resolve the visual lane category for an episode given its
 * `(source_name, episode_type)` pair. Returns `"other"` for any unknown pair.
 *
 * This intentionally accepts strings (not narrow union types) so it can be
 * called against raw API payloads without type assertions. Callers should
 * prefer `episode.category` when the backend supplies it; this helper is the
 * fallback path for older responses where `category` is absent.
 */
export function categoryForSource(
  sourceName: string,
  episodeType: string,
): Category {
  return SOURCE_CATEGORY_MAP[`${sourceName}|${episodeType}`] ?? "other"
}
