/**
 * Typed semantic visual roles.
 *
 * A palette slot is not a meaning. Callers select a role, and this registry
 * owns the token namespace for that role. The Butler identity family is
 * recorded here for spec parity, while its resolver remains private to
 * ButlerMark.
 */

const STATE_TOKENS = [
  "--red",
  "--amber",
  "--green",
  "--dim",
  "--state-unidentified",
  "--muted-foreground",
] as const;

// Keep semantic role values literal. The visual-role guard deliberately
// rejects dynamically assembled var(...) references outside ButlerMark, even
// when their eventual property is a semantic role.
const LOCAL_CATEGORY_VALUES = [
  "var(--categorical-1)",
  "var(--categorical-2)",
  "var(--categorical-3)",
  "var(--categorical-4)",
  "var(--categorical-5)",
  "var(--categorical-6)",
  "var(--categorical-7)",
  "var(--categorical-8)",
  "var(--categorical-9)",
  "var(--categorical-10)",
  "var(--categorical-11)",
  "var(--categorical-12)",
];

const CHART_SERIES_VALUES = [
  "var(--chart-1)",
  "var(--chart-2)",
  "var(--chart-3)",
  "var(--chart-4)",
  "var(--chart-5)",
];

const LOCAL_CATEGORY_TOKENS = LOCAL_CATEGORY_VALUES.map((value) => value.slice(4, -1));
const CHART_SERIES_TOKENS = CHART_SERIES_VALUES.map((value) => value.slice(4, -1));
export const VISUAL_TOKEN_ROLE_REGISTRY = {
  "butler-identity": {
    specRole: "Butler identity",
    resolver: "ButlerMark (private)",
    tokenFamily: "--category-1..12",
    requiredSignal: "letter-mark only",
    slotCount: 12,
    legendRequired: false,
  },
  state: {
    specRole: "Operational state",
    resolver: "StateDot / stateColorVar",
    tokenFamily:
      "--red, --amber, --green, --dim, --state-unidentified, --muted-foreground",
    requiredSignal: "state affordance",
    tokens: STATE_TOKENS,
    legendRequired: false,
  },
  "local-category": {
    specRole: "Local category",
    resolver: "categoricalHueVar / categoricalColor",
    tokenFamily: "--categorical-1..12",
    requiredSignal: "label, icon, position, or legend",
    tokens: LOCAL_CATEGORY_TOKENS,
    values: LOCAL_CATEGORY_VALUES,
    legendRequired: true,
  },
  "chart-series": {
    specRole: "Chart series",
    resolver: "chartSeriesColor / chartColor",
    tokenFamily: "--chart-1..5",
    requiredSignal: "series label or legend",
    tokens: CHART_SERIES_TOKENS,
    values: CHART_SERIES_VALUES,
    legendRequired: true,
  },
  "owner-custom-color": {
    specRole: "Owner custom color",
    resolver: "ownerCustomColor / labelFillColors",
    tokenFamily: "normalized opaque owner hex",
    requiredSignal: "owner label or legend",
    tokens: [],
    acceptedHexLengths: [3, 4, 6, 8],
    normalization: "opaque RGB",
    foregrounds: [
      "--label-fill-foreground-on-light",
      "--label-fill-foreground-on-dark",
    ],
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

export interface LabelFillColors {
  backgroundColor: CategoricalColor | OwnerCustomColor;
  color: string;
}

const CATEGORICAL_FILL_FOREGROUND = "var(--categorical-fill-foreground)";
const LABEL_FILL_FOREGROUND_ON_LIGHT = "var(--label-fill-foreground-on-light)";
const LABEL_FILL_FOREGROUND_ON_DARK = "var(--label-fill-foreground-on-dark)";

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
  const values = VISUAL_TOKEN_ROLE_REGISTRY[role].values;
  const slot = ((index % values.length) + values.length) % values.length;
  return values[slot]!;
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

/**
 * Normalize an owner-selected hex color into an opaque RGB fill.
 *
 * Alpha-bearing CSS hex has no stable contrast guarantee because its effective
 * background depends on the surface below the badge. The four CSS hex forms
 * remain accepted, but #RGBA and #RRGGBBAA intentionally drop alpha so the
 * foreground selection below is deterministic and verifiably accessible.
 */
export function ownerCustomColor(
  value: string | null | undefined,
): OwnerCustomColor | undefined {
  const trimmed = value?.trim();
  const hex = trimmed?.startsWith("#") ? trimmed.slice(1) : "";
  const acceptedLengths = VISUAL_TOKEN_ROLE_REGISTRY["owner-custom-color"].acceptedHexLengths;
  if (
    !hex ||
    !/^[0-9a-f]+$/i.test(hex) ||
    !acceptedLengths.some((length) => length === hex.length)
  ) {
    return undefined;
  }

  const rgb = hex.length <= 4
    ? hex.slice(0, 3).split("").map((component) => component + component).join("")
    : hex.slice(0, 6);
  return `#${rgb.toLowerCase()}` as OwnerCustomColor;
}

function srgbComponentLuminance(component: number): number {
  const normalized = component / 255;
  return normalized <= 0.04045
    ? normalized / 12.92
    : ((normalized + 0.055) / 1.055) ** 2.4;
}

function ownerCustomColorLuminance(color: OwnerCustomColor): number {
  const red = Number.parseInt(color.slice(1, 3), 16);
  const green = Number.parseInt(color.slice(3, 5), 16);
  const blue = Number.parseInt(color.slice(5, 7), 16);
  return (
    0.2126 * srgbComponentLuminance(red) +
    0.7152 * srgbComponentLuminance(green) +
    0.0722 * srgbComponentLuminance(blue)
  );
}

function ownerCustomFillForeground(color: OwnerCustomColor): string {
  const luminance = ownerCustomColorLuminance(color);
  const contrastWithDark = (luminance + 0.05) / 0.05;
  const contrastWithLight = 1.05 / (luminance + 0.05);
  return contrastWithDark >= contrastWithLight
    ? LABEL_FILL_FOREGROUND_ON_LIGHT
    : LABEL_FILL_FOREGROUND_ON_DARK;
}

/** Resolve the foreground token for a non-owner categorical fill. */
export function categoricalFillForeground(): string {
  return CATEGORICAL_FILL_FOREGROUND;
}

/**
 * Resolve a label's complete fill style at the role boundary.
 *
 * Custom owner hex values receive a foreground selected from their normalized
 * opaque background. Unsupported input falls back to the local categorical
 * role, whose foreground is theme-aware and contrast-tested for every slot.
 */
export function labelFillColors(
  labelName: string,
  ownerColor: string | null | undefined,
): LabelFillColors {
  const customColor = ownerCustomColor(ownerColor);
  if (customColor) {
    return {
      backgroundColor: customColor,
      color: ownerCustomFillForeground(customColor),
    };
  }
  return {
    backgroundColor: categoricalHueVar(labelName),
    color: categoricalFillForeground(),
  };
}

export const CATEGORICAL_TOKEN_COUNT =
  VISUAL_TOKEN_ROLE_REGISTRY["local-category"].tokens.length;
