import { ESLint } from "eslint";
import { describe, expect, it } from "vitest";

const CATEGORY_IDENTITY_TOKEN = `var(--${"category-1"})`;
const LEAKED_UI_SOURCE =
  `export const leaked = ${JSON.stringify(CATEGORY_IDENTITY_TOKEN)};\n`;

// These are the standard Tailwind v4 color-utility spellings that accept the
// parenthesized CSS-variable form, e.g. `bg-(--token)`. The guard must not
// leave a less-common color channel as an identity-token escape hatch.
const IDENTITY_COLOR_UTILITY_SPELLINGS = [
  "bg",
  "text",
  "decoration",
  "border",
  "border-x",
  "border-y",
  "border-s",
  "border-e",
  "border-t",
  "border-r",
  "border-b",
  "border-l",
  "divide",
  "outline",
  "ring",
  "ring-offset",
  "shadow",
  "inset-shadow",
  "inset-ring",
  "drop-shadow",
  "text-shadow",
  "accent",
  "caret",
  "fill",
  "stroke",
  "from",
  "via",
  "to",
  "placeholder",
] as const;
const IDENTITY_SLOTS = Array.from({ length: 12 }, (_, index) => index + 1);
const IDENTITY_VARIABLES = IDENTITY_SLOTS.flatMap((slot) => [
  `category-${slot}`,
  `color-category-${slot}`,
]);
const IDENTITY_UTILITY_VALUES = [
  ...IDENTITY_VARIABLES.map((variable) => `var(--${variable})`),
  ...IDENTITY_COLOR_UTILITY_SPELLINGS.flatMap((utility) =>
    IDENTITY_VARIABLES.map((variable) => `${utility}-${variable}`),
  ),
  ...IDENTITY_COLOR_UTILITY_SPELLINGS.flatMap((utility) =>
    IDENTITY_VARIABLES.map((variable) => `${utility}-(--${variable})`),
  ),
];
const BUTLER_MARK_IDENTITY_UTILITY_VALUES = [
  `bg-(--${["category", 1].join("-")})`,
  `text-(--${["color", "category", 12].join("-")})`,
];
const SEMANTIC_ROLE_SOURCE = [
  'import { categoricalColor, categoricalHueVar, stateColorVar } from "@/lib/visual-token-roles";',
  'import { chartSeriesColor } from "@/lib/chart-colors";',
  'export const colors = [categoricalColor(0), categoricalHueVar("family"), stateColorVar("healthy"), chartSeriesColor(0)];',
  'export const className = "bg-(--categorical-1)";',
].join("\n");

function sourceWithStringLiterals(values: readonly string[]): string {
  return values
    .map((value, index) => `export const value${index} = ${JSON.stringify(value)};`)
    .join("\n");
}

function sourceWithTemplateLiterals(values: readonly string[]): string {
  return values
    .map((value, index) => `export const value${index} = \`${value}\`;`)
    .join("\n");
}

async function visualRoleMessages(source: string, filePath: string) {
  const [result] = await new ESLint().lintText(source, { filePath });
  return result.messages.filter(
    (message) =>
      message.ruleId === "no-restricted-syntax" &&
      message.message.includes("Butler identity"),
  );
}

describe("semantic visual-role lint", () => {
  it("rejects identity tokens in non-ButlerMark UI files", async () => {
    const eslint = new ESLint();
    const [result] = await eslint.lintText(LEAKED_UI_SOURCE, {
      filePath: "src/components/ui/IdentityLeak.tsx",
    });

    expect(result.messages).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          ruleId: "no-restricted-syntax",
          message: expect.stringContaining(
            "Butler identity tokens are private to ButlerMark",
          ),
        }),
      ]),
    );
  });

  it.each([
    ["string literals", sourceWithStringLiterals],
    ["template literals", sourceWithTemplateLiterals],
  ])(
    "rejects every identity variable and supported Tailwind color utility form in %s",
    async (_literalKind, sourceBuilder) => {
      const roleMessages = await visualRoleMessages(
        sourceBuilder(IDENTITY_UTILITY_VALUES),
        "src/components/ui/IdentityAliasLeak.tsx",
      );

      expect(roleMessages).toHaveLength(IDENTITY_UTILITY_VALUES.length);
    },
  );

  it("keeps the canonical ButlerMark exemption narrow", async () => {
    const roleMessages = await visualRoleMessages(
      sourceWithStringLiterals(BUTLER_MARK_IDENTITY_UTILITY_VALUES),
      "src/components/ui/ButlerMark.tsx",
    );

    expect(roleMessages).toEqual([]);
  });

  it("permits typed semantic role helpers and their non-identity tokens", async () => {
    const roleMessages = await visualRoleMessages(
      SEMANTIC_ROLE_SOURCE,
      "src/components/ui/SemanticRoleConsumer.tsx",
    );

    expect(roleMessages).toEqual([]);
  });
});
