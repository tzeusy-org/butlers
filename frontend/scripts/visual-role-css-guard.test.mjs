import { describe, expect, it } from "vitest";
import { compile } from "tailwindcss";

import {
  DYNAMIC_VALUE_MARKER,
  TAILWIND_COLOR_UTILITY_SPELLINGS,
  findPrivateIdentityReferences,
  normalizeCssCustomProperty,
} from "./visual-role-css-guard.mjs";

function references(value) {
  return findPrivateIdentityReferences(value).map(
    ({ form, property, ambiguous }) => ({ form, property, ambiguous }),
  );
}

describe("visual-role CSS custom-property grammar", () => {
  it.each([
    ["canonical private property", "--category-1", "--category-1"],
    ["legacy private property", "--color-category-12", "--color-category-12"],
    ["hex-escaped identifier character", "--\\63 ategory-1", "--category-1"],
    ["hex-escaped leading hyphens", "\\2d\\2d category-1", "--category-1"],
    ["hex-escaped slot", "--category-\\31", "--category-1"],
    ["semantic local category", "--categorical-1", "--categorical-1"],
  ])("normalizes %s", (_description, source, expected) => {
    expect(normalizeCssCustomProperty(source)).toBe(expected);
  });

  it.each([
    ["canonical", "var(--category-1)", "--category-1"],
    ["whitespace", "var( --color-category-12)", "--color-category-12"],
    ["newline", "var(\n--category-1)", "--category-1"],
    ["comment trivia", "var(/* identity */ --color-category-12)", "--color-category-12"],
    ["escaped name", "var(--\\63 ategory-1)", "--category-1"],
    ["escaped var function", "\\76 ar(--category-1)", "--category-1"],
  ])("recognizes CSS var() %s references", (_description, value, property) => {
    expect(references(value)).toEqual([
      { form: "css-var", property, ambiguous: false },
    ]);
  });

  it("recognizes direct and color-hinted Tailwind forms for every supported utility", () => {
    for (const utility of TAILWIND_COLOR_UTILITY_SPELLINGS) {
      expect(references(`${utility}-(--category-1)`)).toEqual([
        {
          form: "tailwind-parenthesized",
          property: "--category-1",
          ambiguous: false,
        },
      ]);
      expect(references(`hover:${utility}-(color:--color-category-12)`)).toEqual([
        {
          form: "tailwind-parenthesized",
          property: "--color-category-12",
          ambiguous: false,
        },
      ]);
    }
  });

  it("pins the supported utility matrix to Tailwind v4 direct and color-hinted output", async () => {
    for (const utility of TAILWIND_COLOR_UTILITY_SPELLINGS) {
      for (const property of ["--category-1", "--color-category-1"]) {
        for (const candidate of [
          `${utility}-(${property})`,
          `${utility}-(color:${property})`,
        ]) {
          const compiler = await compile("@tailwind utilities;");
          expect(compiler.build([candidate])).toContain(`var(${property})`);
        }
      }
    }
  });

  it("recognizes supported named Tailwind aliases", () => {
    expect(references("focus:ring-category-1")).toEqual([
      {
        form: "tailwind-named-alias",
        property: "--category-1",
        ambiguous: false,
      },
    ]);
    expect(references("hover:bg-color-category-12/70")).toEqual([
      {
        form: "tailwind-named-alias",
        property: "--color-category-12",
        ambiguous: false,
      },
    ]);
  });

  it("fails closed only after a dynamic value enters a private namespace", () => {
    expect(references(`var(--category-${DYNAMIC_VALUE_MARKER})`)).toEqual([
      { form: "css-var", property: "--category-", ambiguous: true },
    ]);
    expect(references(`ring-color-category-${DYNAMIC_VALUE_MARKER}`)).toEqual([
      {
        form: "tailwind-named-alias",
        property: "--color-category-",
        ambiguous: true,
      },
    ]);
    expect(references(`var(--${DYNAMIC_VALUE_MARKER})`)).toEqual([]);
    expect(references(`border-${DYNAMIC_VALUE_MARKER}`)).toEqual([]);
  });

  it("preserves semantic-role and out-of-range custom properties", () => {
    expect(references("var(--categorical-1)")).toEqual([]);
    expect(references("bg-(color:--categorical-1)")).toEqual([]);
    expect(references("var(--category-13)")).toEqual([]);
    expect(references("text-color-category-13")).toEqual([]);
  });
});
