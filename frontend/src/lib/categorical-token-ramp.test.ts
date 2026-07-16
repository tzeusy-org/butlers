import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const CSS_PATH = fileURLToPath(new URL("../index.css", import.meta.url));
const CSS_SOURCE = readFileSync(CSS_PATH, "utf-8");
const SRC_ROOT = fileURLToPath(new URL("../", import.meta.url));

const CATEGORICAL_SLOTS = Array.from({ length: 12 }, (_, index) => index + 1);

const MIGRATED_CONSUMERS = [
  "components/general/ComplexityBadge.tsx",
  "components/chronicles/lane-taxonomy.ts",
  "components/butler-detail/ButlerHealthMeasurementsTab.tsx",
  "lib/calendar-overlays.ts",
  "components/general/JsonViewer.tsx",
] as const;

function extractTopLevelBlock(source: string, selector: string): string {
  const startPattern = new RegExp(`^${selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")} \\{$`, "m");
  const startMatch = startPattern.exec(source);
  if (!startMatch) {
    throw new Error(`Could not find "${selector} {" block in index.css`);
  }
  const bodyStart = startMatch.index + startMatch[0].length;
  const closeIndex = source.indexOf("\n}", bodyStart);
  if (closeIndex === -1) {
    throw new Error(`Could not find closing "}" for ${selector}`);
  }
  return source.slice(bodyStart, closeIndex);
}

function categoricalSlots(block: string): number[] {
  return [...block.matchAll(/--categorical-(\d+):\s*oklch\(/g)]
    .map((match) => Number(match[1]))
    .sort((left, right) => left - right);
}

describe("categorical token ramp", () => {
  it.each([
    [":root", extractTopLevelBlock(CSS_SOURCE, ":root")],
    [".dark", extractTopLevelBlock(CSS_SOURCE, ".dark")],
  ])("defines all 12 text-safe categorical slots in %s", (_theme, block) => {
    expect(categoricalSlots(block)).toEqual(CATEGORICAL_SLOTS);
  });

  it("exposes every categorical slot to Tailwind", () => {
    for (const slot of CATEGORICAL_SLOTS) {
      expect(CSS_SOURCE).toMatch(
        new RegExp(
          `--color-categorical-${slot}:\\s*var\\(--categorical-${slot}\\);`,
        ),
      );
    }
  });

  it.each(MIGRATED_CONSUMERS)("migrates %s without a palette-rule exemption", (path) => {
    const source = readFileSync(`${SRC_ROOT}${path}`, "utf-8");

    expect(source).toContain("categorical-");
    expect(source).not.toContain("no-restricted-syntax");
  });
});
