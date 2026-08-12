/**
 * Typed semantic visual roles.
 *
 * A palette slot is not a meaning. Callers select a role, and this registry
 * owns the token namespace for that role. Butler identity is intentionally
 * absent: resolving a butler's identity belongs only to ButlerMark.
 */

const STATE_TOKENS = [
  "--red",
  "--amber",
  "--green",
  "--dim",
  "--state-unidentified",
  "--muted-foreground",
] as const;

const LOCAL_CATEGORY_TOKENS = Array.from(
  { length: 12 },
  (_, index) => `--categorical-${index + 1}`,
);

const CHART_SERIES_TOKENS = Array.from(
  { length: 5 },
  (_, index) => `--chart-${index + 1}`,
);

export const VISUAL_TOKEN_ROLE_REGISTRY = {
  state: {
    tokens: STATE_TOKENS,
    legendRequired: false,
  },
  "local-category": {
    tokens: LOCAL_CATEGORY_TOKENS,
    legendRequired: true,
  },
  "chart-series": {
    tokens: CHART_SERIES_TOKENS,
    legendRequired: true,
  },
  "owner-custom-color": {
    tokens: [],
    legendRequired: true,
  },
} as const;

export type StateColorRole =
  | "healthy"
  | "ok"
  | "degraded"
  | "error"
  | "waiting"
  | "unidentified"
  | "duplicate-candidate"
  | "stale"
  | "archived";

export type CategoricalColor = string & {
  readonly __visualRole: "local-category";
};
export type ChartSeriesColor = string & {
  readonly __visualRole: "chart-series";
};
export type OwnerCustomColor = string & {
  readonly __visualRole: "owner-custom-color";
};

const STATE_COLORS: Record<StateColorRole, string> = {
  healthy: "var(--green)",
  ok: "var(--green)",
  degraded: "var(--amber)",
  error: "var(--red)",
  waiting: "var(--dim)",
  unidentified: "var(--state-unidentified)",
  "duplicate-candidate": "var(--amber)",
  stale: "var(--red)",
  archived: "var(--muted-foreground)",
};

function hashName(name: string): number {
  let hash = 0;
  for (let index = 0; index < name.length; index += 1) {
    hash = (hash * 31 + name.charCodeAt(index)) | 0;
  }
  return Math.abs(hash);
}

/** Resolve a slot from an executable non-state role registry. */
export function visualRoleToken(
  role: "local-category" | "chart-series",
  index: number,
): string {
  const tokens = VISUAL_TOKEN_ROLE_REGISTRY[role].tokens;
  const slot = ((index % tokens.length) + tokens.length) % tokens.length;
  return `var(${tokens[slot]})`;
}

/** Resolve a local taxonomy value through the non-identity categorical ramp. */
export function categoricalHueVar(value: string): CategoricalColor {
  return visualRoleToken("local-category", hashName(value)) as CategoricalColor;
}

/** Resolve a stable categorical slot for registries with fixed positions. */
export function categoricalColor(index: number): CategoricalColor {
  return visualRoleToken("local-category", index) as CategoricalColor;
}

/** Resolve a state through the three-state semantic palette. */
export function stateColorVar(state: StateColorRole): string {
  return STATE_COLORS[state];
}

/** Mark an owner-selected color as an explicit custom-color boundary. */
export function ownerCustomColor(
  value: string | null | undefined,
): OwnerCustomColor | undefined {
  if (
    !value ||
    !/^(?:#[0-9a-f]{3,8}|(?:rgb|hsl|oklch|color-mix)\([^)]*\))$/i.test(
      value.trim(),
    )
  ) {
    return undefined;
  }
  return value.trim() as OwnerCustomColor;
}

export const CATEGORICAL_TOKEN_COUNT =
  VISUAL_TOKEN_ROLE_REGISTRY["local-category"].tokens.length;
