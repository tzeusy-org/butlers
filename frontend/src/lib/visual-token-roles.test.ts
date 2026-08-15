import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import {
  CATEGORICAL_TOKEN_COUNT,
  VISUAL_TOKEN_ROLE_REGISTRY,
  categoricalColor,
  labelFillColors,
  categoricalHueVar,
  ownerCustomColor,
  stateColorVar,
} from "./visual-token-roles";
import { chartSeriesColor } from "./chart-colors";
import type { StateColorRole } from "./visual-token-roles";

const SPEC_PATH = fileURLToPath(
  new URL(
    "../../../openspec/specs/dashboard-design-language/spec.md",
    import.meta.url,
  ),
);
const SPEC = readFileSync(SPEC_PATH, "utf8");
const FRONTEND_TOPOLOGY_PATH = fileURLToPath(
  new URL("../../../about/lay-and-land/frontend.md", import.meta.url),
);
const FRONTEND_TOPOLOGY = readFileSync(FRONTEND_TOPOLOGY_PATH, "utf8");

const STATE_ROLES: readonly StateColorRole[] = [
  "healthy",
  "ok",
  "degraded",
  "error",
  "waiting",
  "unidentified",
  "duplicate-candidate",
  "stale",
  "archived",
];

function tokenName(cssVariable: string): string {
  const match = cssVariable.match(/^var\((--[a-z0-9-]+)\)$/);
  if (!match) throw new Error(`Unexpected CSS variable: ${cssVariable}`);
  return match[1];
}

function specStateTokens(): Set<string> {
  const row = SPEC.split("\n").find((line) =>
    line.startsWith("| Operational state |"),
  );
  if (!row) throw new Error("Could not find the operational-state role row");
  return new Set(
    [...row.matchAll(/`(--[a-z-]+)`/g)].map((match) => match[1]),
  );
}

function specRoleTokens(role: "Local category" | "Chart series"): Set<string> {
  const row = SPEC.split("\n").find((line) => line.startsWith(`| ${role} |`));
  if (!row) throw new Error(`Could not find the ${role} role row`);

  return new Set(
    [...row.matchAll(/`(--[a-z-]+)-(\d+)\.\.(\d+)`/g)].flatMap(
      ([, prefix, first, last]) =>
        Array.from(
          { length: Number(last) - Number(first) + 1 },
          (_, index) => `${prefix}-${Number(first) + index}`,
        ),
    ),
  );
}

function specVisualRoleRows(): Map<string, readonly [string, string, string]> {
  const rows = new Map<string, readonly [string, string, string]>();
  const matrix = SPEC.split("### Requirement: Semantic Visual Role Matrix")[1]
    ?.split("### Requirement:")[0];
  if (!matrix) throw new Error("Could not find the semantic visual role matrix");
  for (const line of matrix.split("\n")) {
    const match = line.match(/^\| ([^|]+?) \| ([^|]+?) \| ([^|]+?) \| ([^|]+?) \|$/);
    if (match && match[1] !== "Role" && match[1] !== "------") {
      rows.set(match[1].trim(), [
        match[2].replaceAll("`", "").trim(),
        match[3].replaceAll("`", "").trim(),
        match[4].replaceAll("`", "").trim(),
      ]);
    }
  }
  return rows;
}

describe("semantic visual role registry", () => {
  it("keeps the executable registry and binding spec table aligned", () => {
    const specRows = specVisualRoleRows();
    expect(new Set(specRows.keys())).toEqual(
      new Set(
        Object.values(VISUAL_TOKEN_ROLE_REGISTRY).map(({ specRole }) => specRole),
      ),
    );
    for (const role of Object.values(VISUAL_TOKEN_ROLE_REGISTRY)) {
      expect(specRows.get(role.specRole)).toEqual([
        role.resolver,
        role.tokenFamily,
        role.requiredSignal,
      ]);
    }
    expect(VISUAL_TOKEN_ROLE_REGISTRY["butler-identity"]).toMatchObject({
      slotCount: 12,
    });
    expect(VISUAL_TOKEN_ROLE_REGISTRY["butler-identity"]).not.toHaveProperty(
      "tokens",
    );
    expect(VISUAL_TOKEN_ROLE_REGISTRY["butler-identity"]).not.toHaveProperty(
      "values",
    );
    expect(VISUAL_TOKEN_ROLE_REGISTRY["local-category"].tokens).toHaveLength(
      12,
    );
    expect(VISUAL_TOKEN_ROLE_REGISTRY["chart-series"].tokens).toHaveLength(5);
    expect(VISUAL_TOKEN_ROLE_REGISTRY["owner-custom-color"]).toMatchObject({
      acceptedHexLengths: [3, 4, 6, 8],
      normalization: "opaque RGB",
      foregrounds: [
        "--label-fill-foreground-on-light",
        "--label-fill-foreground-on-dark",
      ],
    });
  });

  it("covers every stateColorVar output in the executable registry", () => {
    const resolverTokens = new Set(STATE_ROLES.map((role) => tokenName(stateColorVar(role))));

    expect(new Set(VISUAL_TOKEN_ROLE_REGISTRY.state.tokens)).toEqual(resolverTokens);
  });

  it("keeps state resolver tokens aligned with the binding spec", () => {
    expect(new Set(VISUAL_TOKEN_ROLE_REGISTRY.state.tokens)).toEqual(specStateTokens());
  });

  it("keeps categorical and chart helpers aligned with registry and spec sets", () => {
    const localCategoryTokens = new Set(
      VISUAL_TOKEN_ROLE_REGISTRY["local-category"].tokens,
    );
    const chartSeriesTokens = new Set(
      VISUAL_TOKEN_ROLE_REGISTRY["chart-series"].tokens,
    );

    expect(localCategoryTokens).toEqual(specRoleTokens("Local category"));
    expect(chartSeriesTokens).toEqual(specRoleTokens("Chart series"));
    expect(
      new Set(
        Array.from({ length: localCategoryTokens.size }, (_, index) =>
          tokenName(categoricalColor(index)),
        ),
      ),
    ).toEqual(localCategoryTokens);
    expect(
      new Set(
        Array.from({ length: chartSeriesTokens.size }, (_, index) =>
          tokenName(chartSeriesColor(index)),
        ),
      ),
    ).toEqual(chartSeriesTokens);
  });

  it("derives helper slot selection from the executable registry", () => {
    const localCategoryTokens = VISUAL_TOKEN_ROLE_REGISTRY[
      "local-category"
    ].tokens as unknown as string[];
    const localCategoryValues = VISUAL_TOKEN_ROLE_REGISTRY[
      "local-category"
    ].values as unknown as string[];
    const chartSeriesTokens = VISUAL_TOKEN_ROLE_REGISTRY["chart-series"]
      .tokens as unknown as string[];
    const chartSeriesValues = VISUAL_TOKEN_ROLE_REGISTRY["chart-series"]
      .values as unknown as string[];
    localCategoryTokens.push("--categorical-13");
    localCategoryValues.push("var(--categorical-13)");
    chartSeriesTokens.push("--chart-6");
    chartSeriesValues.push("var(--chart-6)");

    try {
      expect(categoricalColor(localCategoryTokens.length - 1)).toBe(
        "var(--categorical-13)",
      );
      expect(chartSeriesColor(chartSeriesTokens.length - 1)).toBe(
        "var(--chart-6)",
      );
    } finally {
      localCategoryTokens.pop();
      localCategoryValues.pop();
      chartSeriesTokens.pop();
      chartSeriesValues.pop();
    }
  });

  it("keeps typed helpers in their declared namespaces", () => {
    expect(categoricalHueVar("family")).toMatch(/^var\(--categorical-\d+\)$/);
    expect(categoricalColor(11)).toBe("var(--categorical-12)");
    expect(stateColorVar("healthy")).toBe("var(--green)");
    expect(ownerCustomColor("#1a73e8")).toBe("#1a73e8");
    expect(ownerCustomColor("bg-blue-500")).toBeUndefined();
    // eslint-disable-next-line visual-role/no-private-identity-token -- Exercises the helper's rejection path with a prohibited caller value.
    expect(ownerCustomColor(`var(--${"category-1"})`)).toBeUndefined();
    expect(CATEGORICAL_TOKEN_COUNT).toBe(12);
  });

  it.each([
    ["#123", "#112233"],
    ["#1234", "#112233"],
    ["#123456", "#123456"],
    ["#12345678", "#123456"],
  ])(
    "normalizes valid owner custom CSS hex colors with length %s",
    (color, expected) => {
      expect(ownerCustomColor(color)).toBe(expected);
    },
  );

  it.each(["#12345", "#1234567"])(
    "rejects owner custom hex colors with unsupported CSS length %s",
    (color) => {
      expect(ownerCustomColor(color)).toBeUndefined();
    },
  );

  it.each([
    ["#000", "#000000", "var(--label-fill-foreground-on-dark)"],
    ["#0000", "#000000", "var(--label-fill-foreground-on-dark)"],
    ["#767676", "#767676", "var(--label-fill-foreground-on-light)"],
    ["#777777", "#777777", "var(--label-fill-foreground-on-light)"],
    ["#787878", "#787878", "var(--label-fill-foreground-on-light)"],
    ["#ffffff", "#ffffff", "var(--label-fill-foreground-on-light)"],
    ["#ffffffff", "#ffffff", "var(--label-fill-foreground-on-light)"],
  ])(
    "selects an AA foreground for valid owner hex %s",
    (input, backgroundColor, color) => {
      expect(labelFillColors("Owner", input)).toEqual({ backgroundColor, color });
    },
  );

  it("uses the categorical fill foreground when owner input is unsupported", () => {
    expect(labelFillColors("Family", "#12345")).toEqual({
      backgroundColor: categoricalHueVar("Family"),
      color: "var(--categorical-fill-foreground)",
    });
  });

  it("keeps frontend topology on private ButlerMark identity and typed role helpers", () => {
    expect(FRONTEND_TOPOLOGY).toContain(
      "`ButlerMark`'s color-role-facing subset of its public surface",
    );
    expect(FRONTEND_TOPOLOGY).toContain("For the complete public prop contract");
    expect(FRONTEND_TOPOLOGY).toContain(
      "The identity-slot resolver is private to ButlerMark",
    );
    expect(FRONTEND_TOPOLOGY).toContain(
      "`categoricalHueVar` / `categoricalColor`",
    );
    expect(FRONTEND_TOPOLOGY).toContain(
      "`chartSeriesColor` / `chartColor`",
    );
    expect(FRONTEND_TOPOLOGY).toContain("`StateDot` / `stateColorVar`");
    expect(FRONTEND_TOPOLOGY).not.toMatch(/\b(?:butlerHueVar|categoryHueVar)\b/);
  });
});
