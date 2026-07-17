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
//
// This fixed 10-slot life-activity taxonomy (nine lanes plus `other`) is not
// an operational-state signal or a Butler identity. It uses the dedicated
// categorical ramp rather than the state or Butler hue vocabularies.
// ---------------------------------------------------------------------------

import type { LucideIcon } from "lucide-react"
import {
  Armchair,
  Bot,
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
  | "butler_ops"
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
   * CSS color value for consumers that cannot use Tailwind classes directly
   * (e.g. recharts SVG fill attributes).
   * Must visually match `colour` above.
   */
  color: string
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
    colour: "bg-categorical-1",
    color: "var(--categorical-1)",
    icon: Moon,
    sortOrder: 0,
  },
  exercise: {
    label: "Exercise",
    colour: "bg-categorical-2",
    color: "var(--categorical-2)",
    icon: Dumbbell,
    sortOrder: 1,
  },
  work: {
    label: "Work",
    colour: "bg-categorical-3",
    color: "var(--categorical-3)",
    icon: Briefcase,
    sortOrder: 2,
  },
  // Butler ops (bu-whhll.14): the butlers' OWN LLM sessions (conversations/
  // tasks), a distinct lane from the owner's Work so butler cron chatter never
  // masquerades as the owner's workday. Rendered right after Work.
  butler_ops: {
    label: "Butler ops",
    colour: "bg-categorical-4",
    color: "var(--categorical-4)",
    icon: Bot,
    sortOrder: 3,
  },
  play: {
    label: "Play",
    colour: "bg-categorical-5",
    color: "var(--categorical-5)",
    icon: Gamepad2,
    sortOrder: 4,
  },
  social: {
    label: "Social",
    colour: "bg-categorical-6",
    color: "var(--categorical-6)",
    icon: Users,
    sortOrder: 5,
  },
  travel: {
    label: "Travel",
    colour: "bg-categorical-7",
    color: "var(--categorical-7)",
    icon: Plane,
    sortOrder: 6,
  },
  eat: {
    label: "Eat",
    colour: "bg-categorical-8",
    color: "var(--categorical-8)",
    icon: Utensils,
    sortOrder: 7,
  },
  rest: {
    label: "Rest",
    colour: "bg-categorical-9",
    color: "var(--categorical-9)",
    icon: Armchair,
    sortOrder: 8,
  },
  other: {
    label: "Other",
    colour: "bg-categorical-10",
    color: "var(--categorical-10)",
    icon: CircleQuestionMark,
    sortOrder: 9,
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
// For core.sessions episodes the backend dispatches by trigger_source; both
// conversations and tasks are the butlers' OWN LLM sessions, so this fallback
// resolves core.sessions|work → "butler_ops" (bu-whhll.14), NOT the owner's
// Work lane. The owner's inferred focus/reading (and occupation) resolve to
// "work". Calendar is omitted: it is the intent layer and resolves to "other".
// ---------------------------------------------------------------------------

const SOURCE_CATEGORY_MAP: Record<string, Category> = {
  "core.sessions|work": "butler_ops",
  "spotify.session_summary|listening_episode": "play",
  "steam.play_history|play_episode": "play",
  "owntracks.points|movement_episode": "travel",
  "owntracks.ssid_presence|presence_episode": "rest",
  "owntracks.ssid_presence|occupation_presence_episode": "work",
  "google_health.measurements|sleep_episode": "sleep",
  "google_health.measurements|workout_episode": "exercise",
  "health.meals|eating_event": "eat",
  "home_assistant.history|presence_episode": "rest",
  "chronicler.focus_inferred|focus_block": "work",
  "chronicler.reading_inferred|reading_block": "work",
  "chronicler.occupation_inferred|occupation_block": "work",
  "activitywatch.window|screen_episode": "work",
  "home_assistant.sensor_activity|room_activity_episode": "rest",
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
