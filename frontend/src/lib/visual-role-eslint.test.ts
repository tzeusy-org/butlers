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
// The supported grammar is deliberately exercised as a matrix rather than a
// handful of examples. Every listed utility accepts both Tailwind v4 forms:
//
//   utility-(--custom-property)
//   utility-(color:--custom-property)
//
// Direct CSS references additionally admit CSS whitespace/comments before the
// property name and CSS escapes anywhere in that name. The guard must compare
// canonical property names, not their source spelling.
const IDENTITY_TAILWIND_UTILITY_MATRIX = [
  ...IDENTITY_COLOR_UTILITY_SPELLINGS.flatMap((utility) =>
    IDENTITY_VARIABLES.flatMap((variable) => [
      `${utility}-(--${variable})`,
      `${utility}-(color:--${variable})`,
    ]),
  ),
];
const IDENTITY_NAMED_TAILWIND_ALIAS_MATRIX = [
  ...IDENTITY_COLOR_UTILITY_SPELLINGS.flatMap((utility) =>
    IDENTITY_VARIABLES.map((variable) => `${utility}-${variable}`),
  ),
];
const IDENTITY_CSS_VARIABLE_REFERENCE_MATRIX = [
  ...IDENTITY_VARIABLES.map((variable) => `var(--${variable})`),
  "var( --category-1)",
  "var(\n--color-category-12)",
  "var(\t/* identity trivia */\n--category-1)",
  "var(/* identity trivia */ --color-category-12)",
  "var(--\\63 ategory-1)",
  "var(--color-\\63 ategory-12)",
  "var(\\2d\\2d category-1)",
  "var(--category-\\31)",
  "\\76 ar(--category-1)",
];
const IDENTITY_ESCAPED_TAILWIND_MATRIX = [
  "bg-(--\\63 ategory-1)",
  "text-(color:--color-\\63 ategory-12)",
  "fill-(\\2d\\2d category-1)",
  "stroke-(color:\\2d\\2d color-category-12)",
];
const IDENTITY_UTILITY_VALUES = [
  ...IDENTITY_CSS_VARIABLE_REFERENCE_MATRIX,
  ...IDENTITY_NAMED_TAILWIND_ALIAS_MATRIX,
  ...IDENTITY_TAILWIND_UTILITY_MATRIX,
  ...IDENTITY_ESCAPED_TAILWIND_MATRIX,
];
const BUTLER_MARK_IDENTITY_UTILITY_VALUES = [
  `bg-(--${["category", 1].join("-")})`,
  `text-(--${["color", "category", 12].join("-")})`,
  "fill-(color:--category-1)",
  "var(/* canonical identity component */ --color-category-12)",
  "stroke-(--\\63 ategory-1)",
];
const SEMANTIC_ROLE_SOURCE = [
  'import { categoricalColor, categoricalHueVar, stateColorVar } from "@/lib/visual-token-roles";',
  'import { chartSeriesColor } from "@/lib/chart-colors";',
  'export const colors = [categoricalColor(0), categoricalHueVar("family"), stateColorVar("healthy"), chartSeriesColor(0)];',
  'export const className = "bg-(--categorical-1)";',
  'export const typeHintedClassName = "text-(color:--categorical-1)";',
  'export const semanticCss = "var(/* semantic role */ --categorical-1)";',
  'export const outOfRangeIdentityNamespace = "bg-(--category-13)";',
  'export const legacyOutOfRangeIdentityNamespace = "var(--color-category-13)";',
  'const categoryTone = "categorical-1";',
  'export const dynamicSemanticRole = `border-${categoryTone}`;',
].join("\n");

const DYNAMIC_IDENTITY_TEMPLATE_SOURCE = [
  "const slot = 1;",
  "export const direct = `var(--category-${slot})`;",
  "export const utility = `hover:bg-(color:--color-category-${slot})`;",
  "export const named = `focus:ring-color-category-${slot}`;",
].join("\n");

const DYNAMIC_CUSTOM_PROPERTY_TEMPLATE_SOURCE = [
  'const identity = "category-1";',
  'export const fromConstant = `var(--${identity})`;',
  'export const splitStatic = `var(--${"category"}-1)`;',
  'export const typeHinted = `bg-(color:--${identity})`;',
].join("\n");

const DYNAMIC_CUSTOM_PROPERTY_LITERAL_SOURCE = [
  'const identity = "category-1";',
  'export const fromConstant = "var(--" + identity + ")";',
  'export const splitStatic = "var(--" + "category" + "-1)";',
].join("\n");

const MALFORMED_PRIVATE_IDENTITY_SOURCE = [
  'export const direct = "var(--category-1";',
  'export const utility = "bg-(color:--color-category-12";',
].join("\n");

function sourceWithStringLiterals(values: readonly string[]): string {
  return values
    .map((value, index) => `export const value${index} = ${JSON.stringify(value)};`)
    .join("\n");
}

function sourceWithTemplateLiterals(values: readonly string[]): string {
  return values
    .map((value, index) => {
      const escaped = value
        .replaceAll("\\", "\\\\")
        .replaceAll("`", "\\`")
        .replaceAll("${", "\\${");
      return `export const value${index} = \`${escaped}\`;`;
    })
    .join("\n");
}

async function visualRoleMessages(source: string, filePath: string) {
  const [result] = await new ESLint().lintText(source, { filePath });
  return result.messages.filter(
    (message) =>
      ["no-restricted-syntax", "visual-role/no-private-identity-token"].includes(
        message.ruleId ?? "",
      ) &&
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
          ruleId: "visual-role/no-private-identity-token",
          message: expect.stringContaining(
            "Butler identity token --category-1 is private to ButlerMark",
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

  it("fails closed for malformed and dynamically ambiguous private identity forms", async () => {
    const malformedMessages = await visualRoleMessages(
      MALFORMED_PRIVATE_IDENTITY_SOURCE,
      "src/components/ui/IdentityMalformedLeak.tsx",
    );
    const dynamicMessages = await visualRoleMessages(
      DYNAMIC_IDENTITY_TEMPLATE_SOURCE,
      "src/components/ui/IdentityDynamicLeak.tsx",
    );

    expect(malformedMessages).toHaveLength(2);
    expect(dynamicMessages).toHaveLength(3);
  });

  it("rejects dynamically constructed custom properties through template and literal paths", async () => {
    const templateMessages = await visualRoleMessages(
      DYNAMIC_CUSTOM_PROPERTY_TEMPLATE_SOURCE,
      "src/components/ui/IdentityDynamicTemplateLeak.tsx",
    );
    const literalMessages = await visualRoleMessages(
      DYNAMIC_CUSTOM_PROPERTY_LITERAL_SOURCE,
      "src/components/ui/IdentityDynamicLiteralLeak.tsx",
    );

    expect(templateMessages).toHaveLength(3);
    expect(literalMessages).toHaveLength(2);
  });

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
