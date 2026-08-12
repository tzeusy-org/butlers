import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import {
  CATEGORICAL_TOKEN_COUNT,
  VISUAL_TOKEN_ROLE_REGISTRY,
  categoricalColor,
  categoricalHueVar,
  ownerCustomColor,
  stateColorVar,
} from "./visual-token-roles";

const SPEC_PATH = fileURLToPath(
  new URL(
    "../../../openspec/specs/dashboard-design-language/spec.md",
    import.meta.url,
  ),
);
const SPEC = readFileSync(SPEC_PATH, "utf8");

describe("semantic visual role registry", () => {
  it("keeps the executable registry and binding spec table aligned", () => {
    expect(SPEC).toContain("| Butler identity | `ButlerMark` (private)");
    expect(SPEC).toContain(
      "| Operational state | `StateDot` / `stateColorVar`",
    );
    expect(SPEC).toContain(
      "| Local category | `categoricalHueVar` / `categoricalColor`",
    );
    expect(SPEC).toContain(
      "| Chart series | `chartSeriesColor` / `chartColor`",
    );
    expect(SPEC).toContain("| Owner custom color | `ownerCustomColor`");
    expect(VISUAL_TOKEN_ROLE_REGISTRY["local-category"].tokens).toHaveLength(
      12,
    );
    expect(VISUAL_TOKEN_ROLE_REGISTRY["chart-series"].tokens).toHaveLength(5);
  });

  it("keeps typed helpers in their declared namespaces", () => {
    expect(categoricalHueVar("family")).toMatch(/^var\(--categorical-\d+\)$/);
    expect(categoricalColor(11)).toBe("var(--categorical-12)");
    expect(stateColorVar("healthy")).toBe("var(--green)");
    expect(ownerCustomColor("#1a73e8")).toBe("#1a73e8");
    expect(ownerCustomColor("bg-blue-500")).toBeUndefined();
    expect(ownerCustomColor(`var(--${"category-1"})`)).toBeUndefined();
    expect(CATEGORICAL_TOKEN_COUNT).toBe(12);
  });
});
