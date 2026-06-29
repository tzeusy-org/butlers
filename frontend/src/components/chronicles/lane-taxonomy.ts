// ---------------------------------------------------------------------------
// Chronicles Lane Taxonomy — bu-ig72b.5 / bu-jomz2 / bu-3n44q5 (IEA reframe)
//
// Source of truth for the visual presentation of each Activity lane.
// Backend (aggregations.py) owns the lane string definitions; this file maps
// those strings to display labels, colours, icons, and sort order.
//
// The dashboard renders life-balance LANES, not data sources. Music/gaming fold
// into Play; calendar is the intent layer and is never counted as a lane. See
// `aggregations.LANES` / `lane_for_category` for the backend contract.
//
// Backend never returns colours, labels, or icons — those live here only.
// ---------------------------------------------------------------------------

import type { LucideIcon } from "lucide-react"
import {
  Armchair,
  Briefcase,
  CircleQuestionMark,
  Dumbbell,
  Gamepad2,
  Moon,
  Plane,
  Users,
  Utensils,
} from "lucide-react"

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/**
 * All stable Activity-lane strings emitted by the chronicler backend, plus the
 * `other` catch-all used as a frontend fallback for unmapped categories (the
 * backend never counts those toward a lane).
 */
export type Category =
  | "sleep"
  | "exercise"
  | "work"
  | "play"
  | "social"
  | "travel"
  | "eat"
  | "rest"
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
  /** Lucide-react icon component associated with this lane. */
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
 * Maps each stable Activity-lane string → visual presentation config.
 *
 * Ordering follows the spec lane order: Sleep · Exercise · Work · Play ·
 * Social · Travel · Eat · Rest, with the `other` catch-all last.
 */
export const LANE_TAXONOMY: Readonly<Record<Category, LaneConfig>> = {
  sleep: {
    label: "Sleep",
    colour: "bg-slate-500",
    hex: "#64748b",
    icon: Moon,
    sortOrder: 0,
  },
  exercise: {
    label: "Exercise",
    colour: "bg-rose-500",
    hex: "#f43f5e",
    icon: Dumbbell,
    sortOrder: 1,
  },
  work: {
    label: "Work",
    colour: "bg-blue-600",
    hex: "#2563eb",
    icon: Briefcase,
    sortOrder: 2,
  },
  play: {
    label: "Play",
    colour: "bg-violet-600",
    hex: "#7c3aed",
    icon: Gamepad2,
    sortOrder: 3,
  },
  social: {
    label: "Social",
    colour: "bg-pink-500",
    hex: "#ec4899",
    icon: Users,
    sortOrder: 4,
  },
  travel: {
    label: "Travel",
    colour: "bg-cyan-500",
    hex: "#06b6d4",
    icon: Plane,
    sortOrder: 5,
  },
  eat: {
    label: "Eat",
    colour: "bg-amber-500",
    hex: "#f59e0b",
    icon: Utensils,
    sortOrder: 6,
  },
  rest: {
    label: "Rest",
    colour: "bg-emerald-600",
    hex: "#059669",
    icon: Armchair,
    sortOrder: 7,
  },
  other: {
    label: "Other",
    colour: "bg-slate-400",
    hex: "#94a3b8",
    icon: CircleQuestionMark,
    sortOrder: 8,
  },
}

// ---------------------------------------------------------------------------
// (source_name, episode_type) → Activity lane mapping
//
// Mirrors `_CATEGORY_MAP` ∘ `_CATEGORY_TO_LANE` in
// `src/butlers/chronicler/aggregations.py`. Used as a frontend fallback when
// the backend has not yet attached a `category` (lane) field to the episode
// response. The backend remains the source of truth — keep this table in sync
// with the backend mapping whenever new sources land.
//
// For core.sessions episodes the backend dispatches by trigger_source, but both
// conversations and tasks fold into the Work lane, so this fallback resolves
// core.sessions|work → "work" directly. Calendar is omitted: it is the intent
// layer and resolves to "other".
// ---------------------------------------------------------------------------

const SOURCE_CATEGORY_MAP: Record<string, Category> = {
  "core.sessions|work": "work",
  "spotify.session_summary|listening_episode": "play",
  "steam.play_history|play_episode": "play",
  "owntracks.points|movement_episode": "travel",
  "google_health.measurements|sleep_episode": "sleep",
  "google_health.measurements|workout_episode": "exercise",
  "health.meals|eating_event": "eat",
  "home_assistant.history|presence_episode": "rest",
  "chronicler.focus_inferred|focus_block": "work",
  "chronicler.reading_inferred|reading_block": "work",
}

/**
 * Resolve the Activity lane for an episode given its
 * `(source_name, episode_type)` pair. Returns `"other"` for any unknown pair
 * (including calendar/intent rows, which are never a lane).
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
