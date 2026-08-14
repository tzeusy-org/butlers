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

const FULLY_DYNAMIC_CUSTOM_PROPERTY_TEMPLATE_SOURCE = [
  'const identity = "--category-1";',
  'const suffix = "category-1";',
  'export const direct = `var(${identity})`;',
  'export const withTrivia = `var(/* identity trivia */ ${identity})`;',
  'export const splitStatic = `var(${"--"}${suffix})`;',
  'export const directUtility = `bg-(${identity})`;',
  'export const typedUtility = `bg-(color:${identity})`;',
  'export const typedUtilityWithTrivia = `bg-(color: /* identity trivia */ ${identity})`;',
].join("\n");

const FULLY_DYNAMIC_CUSTOM_PROPERTY_BINARY_SOURCE = [
  'const identity = "--color-category-12";',
  'export const direct = "var(" + identity + ")";',
  'export const withTrivia = "var(/* identity trivia */ " + identity + ")";',
  'export const directUtility = "bg-(" + identity + ")";',
  'export const typedUtility = "bg-(color:" + identity + ")";',
  'export const typedUtilityWithTrivia = "bg-(color: /* identity trivia */ " + identity + ")";',
].join("\n");

function dynamicParenthesizedIdentitySource(construction: "template" | "binary"): string {
  const lines: string[] = [];
  let index = 0;

  for (const utility of IDENTITY_COLOR_UTILITY_SPELLINGS) {
    for (const variable of IDENTITY_VARIABLES) {
      const identity = `identity${index}`;
      lines.push(`const ${identity} = "--${variable}";`);

      if (construction === "template") {
        lines.push(`export const direct${index} = \`${utility}-(\${${identity}})\`;`);
        lines.push(`export const typed${index} = \`${utility}-(color:\${${identity}})\`;`);
        lines.push(
          `export const typedTrivia${index} = \`${utility}-(color: /* identity trivia */ \${${identity}})\`;`,
        );
      } else {
        lines.push(`export const direct${index} = "${utility}-(" + ${identity} + ")";`);
        lines.push(`export const typed${index} = "${utility}-(color:" + ${identity} + ")";`);
        lines.push(
          `export const typedTrivia${index} = "${utility}-(color: /* identity trivia */ " + ${identity} + ")";`,
        );
      }

      index += 1;
    }
  }

  return lines.join("\n");
}

const DYNAMIC_PARENTHESIZED_IDENTITY_FORM_COUNT =
  IDENTITY_COLOR_UTILITY_SPELLINGS.length * IDENTITY_VARIABLES.length * 3;
const DYNAMIC_PARENTHESIZED_IDENTITY_TEMPLATE_SOURCE =
  dynamicParenthesizedIdentitySource("template");
const DYNAMIC_PARENTHESIZED_IDENTITY_BINARY_SOURCE =
  dynamicParenthesizedIdentitySource("binary");

type StructuralAliasFragment = "literal" | "template" | "binary";
type StructuralExpression = "template" | "binary";

function structuralAliasInitializer(
  fragment: StructuralAliasFragment,
  value: string,
): string {
  if (fragment === "literal") return JSON.stringify(value);
  if (fragment === "template") return `\`${value}\``;

  const [first = "", ...rest] = value;
  return [JSON.stringify(first), ...rest.map((character) => JSON.stringify(character))].join(" + ");
}

function structuralAliasExpression(
  expression: StructuralExpression,
  parts: readonly string[],
): string {
  if (expression === "binary") return parts.join(" + ");
  return `\`${parts
    .map((part) => (part.startsWith('"') ? part.slice(1, -1) : `\${${part}}`))
    .join("")}\``;
}

function aliasedStructuralGrammarSource(
  fragment: StructuralAliasFragment,
  expression: StructuralExpression,
): string {
  const cssOpen = structuralAliasInitializer(fragment, "var(");
  const classOpen = structuralAliasInitializer(fragment, "bg-(");
  const typedClassOpen = structuralAliasInitializer(fragment, "bg-(color:");
  const close = structuralAliasInitializer(fragment, ")");
  const sourceParts = (parts: readonly string[]) =>
    structuralAliasExpression(expression, parts);

  return [
    // The custom-property source is runtime-derived. The grammar fragments
    // below are static and must therefore remain visible to the guard even
    // when they arrive through aliases.
    "function resolveCustomProperty(): string { return \"--categorical-1\"; }",
    "const identity = resolveCustomProperty();",
    `const cssOpen = ${cssOpen};`,
    `const classOpen = ${classOpen};`,
    `const typedClassOpen = ${typedClassOpen};`,
    `const close = ${close};`,
    `export const cssDirect = ${sourceParts(['"var("', "identity", '")"'])};`,
    `export const cssPrefix = ${sourceParts(["cssOpen", "identity", '")"'])};`,
    `export const cssSuffix = ${sourceParts(['"var("', "identity", "close"])};`,
    `export const cssBoth = ${sourceParts(["cssOpen", "identity", "close"])};`,
    `export const classDirect = ${sourceParts(['"bg-("', "identity", '")"'])};`,
    `export const classPrefix = ${sourceParts(["classOpen", "identity", '")"'])};`,
    `export const classSuffix = ${sourceParts(['"bg-("', "identity", "close"])};`,
    `export const classBoth = ${sourceParts(["classOpen", "identity", "close"])};`,
    `export const typedDirect = ${sourceParts(['"bg-(color:"', "identity", '")"'])};`,
    `export const typedPrefix = ${sourceParts(["typedClassOpen", "identity", '")"'])};`,
    `export const typedSuffix = ${sourceParts(['"bg-(color:"', "identity", "close"])};`,
    `export const typedBoth = ${sourceParts(["typedClassOpen", "identity", "close"])};`,
  ].join("\n");
}

const ALIASED_STRUCTURAL_GRAMMAR_CASES = (
  ["literal", "template", "binary"] as const
).flatMap((fragment) =>
  (["template", "binary"] as const).map(
    (expression) =>
      [
        `${fragment} structural aliases through ${expression} expressions`,
        aliasedStructuralGrammarSource(fragment, expression),
      ] as const,
  ),
);
const ALIASED_STRUCTURAL_GRAMMAR_FORM_COUNT = 12;

const ALIASED_STATIC_SEMANTIC_ROLE_SOURCE = [
  'const cssOpen = "var(" as const;',
  'const classOpen = `bg-(color:` as const;',
  'const close = (")" + "") as const;',
  'const semanticRole = "--categorical-1" as const;',
  "export const css = cssOpen + semanticRole + close;",
  "export const utility = `${classOpen}${semanticRole}${close}`;",
].join("\n");

const TYPE_ASSERTED_PRIVATE_ALIAS_SOURCE = [
  'const open = "var(" as const;',
  'const identity = "--category-1" as const;',
  'const close = ")" as const;',
  "export const leaked = open + identity + close;",
].join("\n");

const STATIC_PRIVATE_ALIAS_SOURCE = [
  'const open = "var(";',
  'const identity = "--category-1";',
  'const close = ")";',
  "export const leaked = open + identity + close;",
].join("\n");

const STATIC_CALL_CONSTRUCTION_SOURCE = [
  'const token = "--category-1";',
  'export const fromJoin = ["var(", token, ")"].join("");',
  'const fragments = ["var(", token, ")"] as const;',
  'export const fromAliasedJoin = fragments.join("");',
  'export const fromConcat = "var(".concat("--color-category-12", ")");',
  'export const fromReplace = "var(__token__)".replace("__token__", "--category-1");',
].join("\n");

const STATIC_CALL_CONSTRUCTION_VARIANT_SOURCE = [
  'const identity = `--${"category-1"}`;',
  'const tokenPattern = /__token__/;',
  'export const fromRegExpReplace = "var(__token__)".replace(tokenPattern, identity);',
  'const placeholder = `__${"token"}__`;',
  'export const fromReplaceAll = "bg-(__token__)".replaceAll(placeholder, identity);',
  'export const fromTypedReplaceAll = `bg-(color:${placeholder})`.replaceAll(placeholder, identity);',
  'const fragments = ["var("] as const;',
  'export const fromArrayConcatJoin = fragments.concat(identity, ")").join("");',
].join("\n");

const STATIC_CALL_SEMANTIC_ROLE_SOURCE = [
  'const role = `--${"categorical-1"}`;',
  'const placeholder = "__token__";',
  'export const fromReplaceAll = "bg-(color:__token__)".replaceAll(placeholder, role);',
  'const fragments = ["var("] as const;',
  'const suffix = [role, ")"] as const;',
  'export const fromArrayConcatJoin = fragments.concat(suffix).join("");',
].join("\n");

const IMMUTABLE_ARRAY_WRAPPER_CONSTRUCTION_SOURCE = [
  'const identity = "--category-1";',
  'const fragments = ["var(", identity, ")"] as const;',
  'export const fromMap = fragments.map((part) => part).join("");',
  'export const fromSlice = fragments.slice().join("");',
  'export const fromSpread = [...fragments].join("");',
  'export const fromArrayFrom = Array.from(fragments).join("");',
  'export const fromArrayFromMap = Array.from(fragments, (part) => part).join("");',
  'export const fromFilter = fragments.filter(() => true).join("");',
].join("\n");

const IMMUTABLE_ARRAY_WRAPPER_SEMANTIC_ROLE_SOURCE = [
  'const role = "--categorical-1";',
  'const fragments = ["var(", role, ")"] as const;',
  'export const fromMap = fragments.map((part) => part).join("");',
  'export const fromSlice = fragments.slice().join("");',
  'export const fromSpread = [...fragments].join("");',
  'export const fromArrayFrom = Array.from(fragments).join("");',
  'export const fromArrayFromMap = Array.from(fragments, (part) => part).join("");',
  'export const fromFilter = fragments.filter(() => true).join("");',
].join("\n");

// These are deliberately non-identity transformations. Treating map(),
// filter(), reverse(), or Array.from() as an identity operation misses the
// private token each pipeline creates from harmless-looking fragments.
const TRANSFORMED_STATIC_PRIVATE_IDENTITY_SOURCE = [
  'const mapped = ["var(", "category-", "1)"] as const;',
  'export const fromMap = mapped.map((part) => part.replace("category-", "--category-")).join("");',
  'const filtered = ["var(", "--", "__drop__", "category-1", ")"] as const;',
  'export const fromFilter = filtered.filter((part) => part !== "__drop__").join("");',
  'const reversed = [")", "category-1", "--", "var("] as const;',
  'export const fromReverse = reversed.reverse().join("");',
  'const arrayFrom = ["var(", "category-", "1)"] as const;',
  'export const fromArrayFrom = Array.from(arrayFrom, (part) => part.replace("category-", "--category-")).join("");',
  'const pattern = new RegExp("__token__");',
  'export const fromNewRegExp = "var(__token__)".replace(pattern, "--category-1");',
  'const objectAlias = { open: "var(", property: "--category-1", close: ")" } as const;',
  'export const fromObjectAlias = objectAlias.open + objectAlias.property + objectAlias.close;',
  'const nestedObjectAlias = { parts: ["var(", "--category-1", ")"] as const } as const;',
  'export const fromStaticAt = nestedObjectAlias.parts.at(0)! + nestedObjectAlias.parts.at(1)! + nestedObjectAlias.parts.at(2)!;',
].join("\n");

const TRANSFORMED_STATIC_SEMANTIC_ROLE_SOURCE = [
  'const mapped = ["var(", "categorical-", "1)"] as const;',
  'export const fromMap = mapped.map((part) => part.replace("categorical-", "--categorical-")).join("");',
  'const filtered = ["var(", "--", "__drop__", "categorical-1", ")"] as const;',
  'export const fromFilter = filtered.filter((part) => part !== "__drop__").join("");',
  'const reversed = [")", "categorical-1", "--", "var("] as const;',
  'export const fromReverse = reversed.reverse().join("");',
  'const arrayFrom = ["var(", "categorical-", "1)"] as const;',
  'export const fromArrayFrom = Array.from(arrayFrom, (part) => part.replace("categorical-", "--categorical-")).join("");',
  'const pattern = new RegExp("__token__");',
  'export const fromNewRegExp = "var(__token__)".replace(pattern, "--categorical-1");',
  'const objectAlias = { open: "var(", property: "--categorical-1", close: ")" } as const;',
  'export const fromObjectAlias = objectAlias.open + objectAlias.property + objectAlias.close;',
  'const nestedObjectAlias = { parts: ["var(", "--categorical-1", ")"] as const } as const;',
  'export const fromStaticAt = nestedObjectAlias.parts.at(0)! + nestedObjectAlias.parts.at(1)! + nestedObjectAlias.parts.at(2)!;',
].join("\n");

// A transform that cannot be evaluated exactly is still unsafe when its
// statically known fragments form a custom-property construction: assuming it
// is identity-mapped was the original bypass. The guard must fail closed here
// rather than allow an arbitrary callback to synthesize a Butler token.
const UNRESOLVED_STATIC_TRANSFORM_SOURCE = [
  'const fragments = ["var(", "category-", "1)"] as const;',
  'declare function transform(part: string): string;',
  'export const unresolved = fragments.map(transform).join("");',
].join("\n");

const CSSOM_PRIVATE_IDENTITY_READ_SOURCE = [
  'export const literal = getComputedStyle(document.documentElement).getPropertyValue("--category-1");',
  'const alias = "--color-category-12";',
  'export const fromConst = getComputedStyle(document.documentElement).getPropertyValue(alias);',
  'const prefix = "--category";',
  'const suffix = "-1";',
  'export const fromBinary = getComputedStyle(document.documentElement).getPropertyValue(prefix + suffix);',
].join("\n");

const CSSOM_SEMANTIC_ROLE_READ_SOURCE = [
  'const category = "--categorical-1";',
  'export const allowed = getComputedStyle(document.documentElement).getPropertyValue(category);',
].join("\n");

const CSSOM_COMPUTED_METHOD_PRIVATE_IDENTITY_READ_SOURCE = [
  'export const cssom = getComputedStyle(document.documentElement)["get" + "PropertyValue"]("--category-1");',
  'export const typedOm = document.documentElement.computedStyleMap()["g" + "et"]("--color-category-12");',
].join("\n");

const CSSOM_COMPUTED_METHOD_SEMANTIC_ROLE_READ_SOURCE = [
  'export const cssom = getComputedStyle(document.documentElement)["get" + "PropertyValue"]("--categorical-1");',
  'export const typedOm = document.documentElement.computedStyleMap()["g" + "et"]("--categorical-1");',
].join("\n");

const CSSOM_DYNAMIC_METHOD_PRIVATE_IDENTITY_READ_SOURCE = [
  'declare const method: string;',
  'export const cssom = getComputedStyle(document.documentElement)[method]("--category-1");',
  'export const typedOm = document.documentElement.computedStyleMap()[method]("--color-category-12");',
].join("\n");

const CSSOM_DYNAMIC_METHOD_SEMANTIC_ROLE_READ_SOURCE = [
  'declare const method: string;',
  'export const cssom = getComputedStyle(document.documentElement)[method]("--categorical-1");',
  'export const typedOm = document.documentElement.computedStyleMap()[method]("--categorical-1");',
].join("\n");

const FROM_CODE_POINT_PRIVATE_IDENTITY_SOURCE = [
  'export const fromCodePoint = String.fromCodePoint(118, 97, 114, 40, 45, 45, 99, 97, 116, 101, 103, 111, 114, 121, 45, 49, 41);',
].join("\n");

const FROM_CODE_POINT_SEMANTIC_ROLE_SOURCE = [
  'export const fromCodePoint = String.fromCodePoint(118, 97, 114, 40, 45, 45, 99, 97, 116, 101, 103, 111, 114, 105, 99, 97, 108, 45, 49, 41);',
].join("\n");

const UNMODELLED_STATIC_PRIVATE_RESOLVER_SOURCE = [
  'export const fromCharCode = String.fromCharCode(118, 97, 114, 40).concat("--category-1", ")");',
  'export const fromReduce = ["var(", "--category-1", ")"].reduce((value, part) => value + part, "");',
  'export const fromPrototypeJoin = Array.prototype.join.call(["var(", "--category-1", ")"], "");',
  'export const fromPrototypeConcat = String.prototype.concat.call("var(", "--category-1", ")");',
  'export const ordinaryTailwind = ["bg-(", "--category-1", ")"].reduce((value, part) => value + part, "");',
  'export const typedTailwind = ["bg-(color:", "--color-category-12", ")"].reduce((value, part) => value + part, "");',
].join("\n");

const UNMODELLED_STATIC_SEMANTIC_ROLE_RESOLVER_SOURCE = [
  'export const fromCharCode = String.fromCharCode(118, 97, 114, 40).concat("--categorical-1", ")");',
  'export const fromReduce = ["var(", "--categorical-1", ")"].reduce((value, part) => value + part, "");',
  'export const fromPrototypeJoin = Array.prototype.join.call(["var(", "--categorical-1", ")"], "");',
  'export const fromPrototypeConcat = String.prototype.concat.call("var(", "--categorical-1", ")");',
  'export const ordinaryTailwind = ["bg-(", "--categorical-1", ")"].reduce((value, part) => value + part, "");',
  'export const typedTailwind = ["bg-(color:", "--categorical-1", ")"].reduce((value, part) => value + part, "");',
].join("\n");

const CSSOM_PRIVATE_IDENTITY_RESOLVER_VARIANT_SOURCE = [
  'export const directStyle = document.documentElement.style.getPropertyValue("--category-1");',
  'const read = getComputedStyle;',
  'export const aliasedFunction = read(document.documentElement).getPropertyValue("--color-category-12");',
  'const computed = getComputedStyle(document.documentElement);',
  'export const aliasedStyle = computed.getPropertyValue("--category-1");',
  'export const typedOm = document.documentElement.computedStyleMap().get("--color-category-12");',
  'const inlineStyle = document.documentElement.style;',
  'export const aliasedInlineStyle = inlineStyle.getPropertyValue("--category-1");',
  'export const windowGlobal = window.getComputedStyle(document.documentElement).getPropertyValue("--color-category-12");',
  'const windowRead = window.getComputedStyle;',
  'export const aliasedWindowFunction = windowRead(document.documentElement).getPropertyValue("--category-1");',
  'const typedStyleMap = document.documentElement.computedStyleMap();',
  'export const aliasedTypedOm = typedStyleMap.get("--color-category-12");',
].join("\n");

const CSSOM_SEMANTIC_ROLE_RESOLVER_VARIANT_SOURCE = [
  'export const directStyle = document.documentElement.style.getPropertyValue("--categorical-1");',
  'const read = getComputedStyle;',
  'export const aliasedFunction = read(document.documentElement).getPropertyValue("--categorical-1");',
  'const computed = getComputedStyle(document.documentElement);',
  'export const aliasedStyle = computed.getPropertyValue("--categorical-1");',
  'export const typedOm = document.documentElement.computedStyleMap().get("--categorical-1");',
  'const inlineStyle = document.documentElement.style;',
  'export const aliasedInlineStyle = inlineStyle.getPropertyValue("--categorical-1");',
  'export const windowGlobal = window.getComputedStyle(document.documentElement).getPropertyValue("--categorical-1");',
  'const windowRead = window.getComputedStyle;',
  'export const aliasedWindowFunction = windowRead(document.documentElement).getPropertyValue("--categorical-1");',
  'const typedStyleMap = document.documentElement.computedStyleMap();',
  'export const aliasedTypedOm = typedStyleMap.get("--categorical-1");',
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

async function statusGuardMessages(source: string) {
  const [result] = await new ESLint().lintText(source, {
    filePath: "src/components/topology/TopologyGraph.tsx",
  });
  return result.messages.filter((message) => message.ruleId === "no-restricted-syntax");
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

  it("rejects fully dynamic custom-property names through template and binary paths", async () => {
    const templateMessages = await visualRoleMessages(
      FULLY_DYNAMIC_CUSTOM_PROPERTY_TEMPLATE_SOURCE,
      "src/components/ui/IdentityFullyDynamicTemplateLeak.tsx",
    );
    const binaryMessages = await visualRoleMessages(
      FULLY_DYNAMIC_CUSTOM_PROPERTY_BINARY_SOURCE,
      "src/components/ui/IdentityFullyDynamicBinaryLeak.tsx",
    );

    expect(templateMessages).toHaveLength(6);
    expect(binaryMessages).toHaveLength(5);
  });

  it.each([
    ["template literals", DYNAMIC_PARENTHESIZED_IDENTITY_TEMPLATE_SOURCE],
    ["binary expressions", DYNAMIC_PARENTHESIZED_IDENTITY_BINARY_SOURCE],
  ])(
    "rejects every fully dynamic direct, typed, and trivia parenthesized form in %s",
    async (_construction, source) => {
      const roleMessages = await visualRoleMessages(
        source,
        "src/components/ui/IdentityDynamicParenthesizedLeak.tsx",
      );

      expect(roleMessages).toHaveLength(DYNAMIC_PARENTHESIZED_IDENTITY_FORM_COUNT);
    },
  );

  it.each(ALIASED_STRUCTURAL_GRAMMAR_CASES)(
    "fails closed when %s surround a runtime-derived custom-property value",
    async (_construction, source) => {
      const roleMessages = await visualRoleMessages(
        source,
        "src/components/ui/IdentityAliasedStructureLeak.tsx",
      );

      expect(roleMessages).toHaveLength(ALIASED_STRUCTURAL_GRAMMAR_FORM_COUNT);
    },
  );

  it("permits statically resolved semantic-role values through structural aliases", async () => {
    const roleMessages = await visualRoleMessages(
      ALIASED_STATIC_SEMANTIC_ROLE_SOURCE,
      "src/components/ui/SemanticRoleAliasedStructure.tsx",
    );

    expect(roleMessages).toEqual([]);
  });

  it("traces type-asserted private aliases", async () => {
    const roleMessages = await visualRoleMessages(
      TYPE_ASSERTED_PRIVATE_ALIAS_SOURCE,
      "src/components/ui/TypeAssertedIdentityAliasLeak.tsx",
    );

    expect(roleMessages).toHaveLength(1);
  });

  it("rejects the direct immutable alias construction", async () => {
    const roleMessages = await visualRoleMessages(
      STATIC_PRIVATE_ALIAS_SOURCE,
      "src/components/ui/StaticIdentityAliasLeak.tsx",
    );

    expect(roleMessages).toHaveLength(1);
  });

  it("rejects statically constructed private references through string calls", async () => {
    const roleMessages = await visualRoleMessages(
      STATIC_CALL_CONSTRUCTION_SOURCE,
      "src/components/ui/StaticCallIdentityAliasLeak.tsx",
    );

    expect(roleMessages).toHaveLength(4);
  });

  it("rejects static RegExp replacement, replaceAll, and array concat/join identity construction", async () => {
    const roleMessages = await visualRoleMessages(
      STATIC_CALL_CONSTRUCTION_VARIANT_SOURCE,
      "src/components/ui/StaticCallIdentityVariantLeak.tsx",
    );

    expect(roleMessages).toHaveLength(4);
    expect(roleMessages.map((message) => message.line)).toEqual([3, 5, 6, 8]);
  });

  it("permits static call construction of semantic-role values", async () => {
    const roleMessages = await visualRoleMessages(
      STATIC_CALL_SEMANTIC_ROLE_SOURCE,
      "src/components/ui/StaticCallSemanticRole.tsx",
    );

    expect(roleMessages).toEqual([]);
  });

  it("rejects unmodelled static resolver constructions of private identity references", async () => {
    const roleMessages = await visualRoleMessages(
      UNMODELLED_STATIC_PRIVATE_RESOLVER_SOURCE,
      "src/components/ui/UnmodelledStaticIdentityResolverLeak.tsx",
    );

    expect(roleMessages).toHaveLength(6);
    expect(roleMessages.map((message) => message.line)).toEqual([1, 2, 3, 4, 5, 6]);
  });

  it("permits the same static resolver constructions for semantic role tokens", async () => {
    const roleMessages = await visualRoleMessages(
      UNMODELLED_STATIC_SEMANTIC_ROLE_RESOLVER_SOURCE,
      "src/components/ui/UnmodelledStaticSemanticRoleResolver.tsx",
    );

    expect(roleMessages).toEqual([]);
  });

  it("rejects immutable array wrapper pipelines that construct private identity references", async () => {
    const roleMessages = await visualRoleMessages(
      IMMUTABLE_ARRAY_WRAPPER_CONSTRUCTION_SOURCE,
      "src/components/ui/ImmutableArrayWrapperIdentityLeak.tsx",
    );

    expect(roleMessages).toHaveLength(6);
    expect(roleMessages.map((message) => message.line)).toEqual([3, 4, 5, 6, 7, 8]);
  });

  it("permits immutable array wrapper pipelines that construct semantic-role values", async () => {
    const roleMessages = await visualRoleMessages(
      IMMUTABLE_ARRAY_WRAPPER_SEMANTIC_ROLE_SOURCE,
      "src/components/ui/ImmutableArrayWrapperSemanticRole.tsx",
    );

    expect(roleMessages).toEqual([]);
  });

  it("rejects statically transformed arrays, RegExp replacements, and object aliases that compose private identity tokens", async () => {
    const roleMessages = await visualRoleMessages(
      TRANSFORMED_STATIC_PRIVATE_IDENTITY_SOURCE,
      "src/components/ui/TransformedStaticIdentityLeak.tsx",
    );

    expect(roleMessages).toHaveLength(7);
    expect(roleMessages.map((message) => message.line)).toEqual([2, 4, 6, 8, 10, 12, 14]);
  });

  it("permits the same statically transformed constructions when they resolve to the categorical role", async () => {
    const roleMessages = await visualRoleMessages(
      TRANSFORMED_STATIC_SEMANTIC_ROLE_SOURCE,
      "src/components/ui/TransformedStaticSemanticRole.tsx",
    );

    expect(roleMessages).toEqual([]);
  });

  it("fails closed when an unresolvable static array transform has private-token construction fragments", async () => {
    const roleMessages = await visualRoleMessages(
      UNRESOLVED_STATIC_TRANSFORM_SOURCE,
      "src/components/ui/UnresolvedStaticIdentityTransform.tsx",
    );

    expect(roleMessages).toHaveLength(1);
    expect(roleMessages[0]?.line).toBe(3);
  });

  it("rejects direct CSSOM reads of private identity properties through literal and static aliases", async () => {
    const roleMessages = await visualRoleMessages(
      CSSOM_PRIVATE_IDENTITY_READ_SOURCE,
      "src/components/ui/IdentityCssomReadLeak.tsx",
    );

    expect(roleMessages).toHaveLength(3);
    expect(roleMessages.map((message) => message.line)).toEqual([1, 3, 6]);
  });

  it("permits CSSOM reads of a static non-identity semantic token", async () => {
    const roleMessages = await visualRoleMessages(
      CSSOM_SEMANTIC_ROLE_READ_SOURCE,
      "src/components/ui/SemanticCssomRead.tsx",
    );

    expect(roleMessages).toEqual([]);
  });

  it("rejects private CSSOM and Typed OM reads through statically composed method keys", async () => {
    const roleMessages = await visualRoleMessages(
      CSSOM_COMPUTED_METHOD_PRIVATE_IDENTITY_READ_SOURCE,
      "src/components/ui/IdentityComputedMethodReadLeak.tsx",
    );

    expect(roleMessages).toHaveLength(2);
    expect(roleMessages.map((message) => message.line)).toEqual([1, 2]);
  });

  it("permits semantic CSSOM and Typed OM reads through statically composed method keys", async () => {
    const roleMessages = await visualRoleMessages(
      CSSOM_COMPUTED_METHOD_SEMANTIC_ROLE_READ_SOURCE,
      "src/components/ui/SemanticComputedMethodRead.tsx",
    );

    expect(roleMessages).toEqual([]);
  });

  it("fails closed for a private identity argument on an unresolved CSSOM or Typed OM method", async () => {
    const roleMessages = await visualRoleMessages(
      CSSOM_DYNAMIC_METHOD_PRIVATE_IDENTITY_READ_SOURCE,
      "src/components/ui/IdentityDynamicMethodReadLeak.tsx",
    );

    expect(roleMessages).toHaveLength(2);
    expect(roleMessages.map((message) => message.line)).toEqual([2, 3]);
  });

  it("does not flag an unresolved CSSOM or Typed OM method with a semantic role argument", async () => {
    const roleMessages = await visualRoleMessages(
      CSSOM_DYNAMIC_METHOD_SEMANTIC_ROLE_READ_SOURCE,
      "src/components/ui/SemanticDynamicMethodRead.tsx",
    );

    expect(roleMessages).toEqual([]);
  });

  it("rejects a private identity string constructed with String.fromCodePoint", async () => {
    const roleMessages = await visualRoleMessages(
      FROM_CODE_POINT_PRIVATE_IDENTITY_SOURCE,
      "src/components/ui/IdentityFromCodePointLeak.tsx",
    );

    expect(roleMessages).toHaveLength(1);
    expect(roleMessages[0]?.line).toBe(1);
  });

  it("permits a semantic categorical string constructed with String.fromCodePoint", async () => {
    const roleMessages = await visualRoleMessages(
      FROM_CODE_POINT_SEMANTIC_ROLE_SOURCE,
      "src/components/ui/SemanticFromCodePoint.tsx",
    );

    expect(roleMessages).toEqual([]);
  });

  it("rejects direct, aliased, and Typed OM private identity property reads", async () => {
    const roleMessages = await visualRoleMessages(
      CSSOM_PRIVATE_IDENTITY_RESOLVER_VARIANT_SOURCE,
      "src/components/ui/IdentityCssomResolverVariantLeak.tsx",
    );

    expect(roleMessages).toHaveLength(8);
    expect(roleMessages.map((message) => message.line)).toEqual([1, 3, 5, 6, 8, 9, 11, 13]);
  });

  it("permits direct, aliased, and Typed OM semantic role property reads", async () => {
    const roleMessages = await visualRoleMessages(
      CSSOM_SEMANTIC_ROLE_RESOLVER_VARIANT_SOURCE,
      "src/components/ui/SemanticCssomResolverVariant.tsx",
    );

    expect(roleMessages).toEqual([]);
  });

  it("keeps the ButlerMark exemption limited to its canonical component", async () => {
    const roleMessages = await visualRoleMessages(
      FULLY_DYNAMIC_CUSTOM_PROPERTY_TEMPLATE_SOURCE,
      "src/components/ui/ButlerMark.tsx",
    );

    expect(roleMessages).toEqual([]);
  });

  it("keeps the ButlerMark exemption for static resolver and CSSOM identity access", async () => {
    const roleMessages = await visualRoleMessages(
      [
        UNMODELLED_STATIC_PRIVATE_RESOLVER_SOURCE,
        CSSOM_PRIVATE_IDENTITY_RESOLVER_VARIANT_SOURCE,
      ].join("\n"),
      "src/components/ui/ButlerMark.tsx",
    );

    expect(roleMessages).toEqual([]);
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

  it("describes category tokens as private Butler identity rather than categorical status hues", async () => {
    const [message] = await statusGuardMessages('const leaked = "var(--category-1)";');

    expect(message?.message).toContain("private Butler identity token");
    expect(message?.message).not.toContain("chart/categorical hue");
    expect(message?.message).not.toContain("fixed identity hue");
    expect(message?.message).not.toContain("exception");
  });
});
