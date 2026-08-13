import { readdirSync, readFileSync } from "node:fs";
import { dirname, extname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

/**
 * Semantic recognizer for the private Butler identity token surface.
 *
 * This deliberately owns only the small grammar the visual-role boundary
 * needs, instead of treating a raw text match as CSS. The recognized forms
 * are:
 *
 *   css-var              := var( css-trivia* custom-property ... )
 *   tailwind-parenthesized := color-utility -( [color:] custom-property )
 *   tailwind-named-alias := color-utility - identity-alias
 *
 * `custom-property` and the `var` function identifier are normalized using
 * CSS identifier escape rules before comparison with the private namespace.
 * CSS whitespace and comments are accepted only where CSS allows trivia
 * (before the var() property name or after a Tailwind type hint). The
 * parenthesized Tailwind grammar is limited to the documented direct and
 * `color:` forms for the maintained color utility list below.
 */

const PRIVATE_IDENTITY_CUSTOM_PROPERTIES = new Set(
  Array.from({ length: 12 }, (_, index) => index + 1).flatMap((slot) => [
    `--category-${slot}`,
    `--color-category-${slot}`,
  ]),
);

const TAILWIND_IDENTITY_ALIASES = new Set(
  [...PRIVATE_IDENTITY_CUSTOM_PROPERTIES].map((property) => property.slice(2)),
);

// This is the supported color-utility grammar pinned by
// visual-role-css-guard.test.mjs and visual-role-eslint.test.ts. Keep it in
// one executable list so no utility spelling becomes an identity escape hatch.
export const TAILWIND_COLOR_UTILITY_SPELLINGS = Object.freeze([
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
]);

const TAILWIND_COLOR_UTILITIES_BY_LENGTH = [
  ...TAILWIND_COLOR_UTILITY_SPELLINGS,
].sort((left, right) => right.length - left.length);

// TemplateLiteral quasis are joined with this marker by the ESLint rule. It
// is not a legal CSS identifier character, so an identity-shaped reference
// spanning an interpolation is intentionally treated as ambiguous and fails
// closed rather than becoming an escape hatch.
export const DYNAMIC_VALUE_MARKER = "\0";

function isCssWhitespace(value, index) {
  switch (value.charCodeAt(index)) {
    case 0x0009: // tab
    case 0x000a: // line feed
    case 0x000c: // form feed
    case 0x000d: // carriage return
    case 0x0020: // space
      return true;
    default:
      return false;
  }
}

function consumeCssWhitespace(value, index) {
  if (value.charCodeAt(index) === 0x000d && value.charCodeAt(index + 1) === 0x000a) {
    return index + 2;
  }
  return index + 1;
}

function isAsciiHexDigit(value, index) {
  const code = value.charCodeAt(index);
  return (
    (code >= 0x0030 && code <= 0x0039) ||
    (code >= 0x0041 && code <= 0x0046) ||
    (code >= 0x0061 && code <= 0x0066)
  );
}

function isCssNameCodePoint(value, index) {
  const code = value.codePointAt(index);
  if (code === undefined) return false;
  return (
    (code >= 0x0030 && code <= 0x0039) ||
    (code >= 0x0041 && code <= 0x005a) ||
    (code >= 0x0061 && code <= 0x007a) ||
    code === 0x002d ||
    code === 0x005f ||
    code >= 0x0080
  );
}

function codePointLength(value, index) {
  const code = value.codePointAt(index);
  return code !== undefined && code > 0xffff ? 2 : 1;
}

function consumeCssEscape(value, start) {
  let index = start + 1;
  if (index >= value.length || isCssWhitespace(value, index)) {
    return { valid: false, end: index, value: "" };
  }

  if (!isAsciiHexDigit(value, index)) {
    const length = codePointLength(value, index);
    return {
      valid: true,
      end: index + length,
      value: value.slice(index, index + length),
    };
  }

  const hexStart = index;
  while (index < value.length && index - hexStart < 6 && isAsciiHexDigit(value, index)) {
    index += 1;
  }
  const code = Number.parseInt(value.slice(hexStart, index), 16);
  if (isCssWhitespace(value, index)) {
    index = consumeCssWhitespace(value, index);
  }

  const normalizedCodePoint =
    code === 0 || code > 0x10ffff || (code >= 0xd800 && code <= 0xdfff)
      ? 0xfffd
      : code;
  return {
    valid: true,
    end: index,
    value: String.fromCodePoint(normalizedCodePoint),
  };
}

function consumeCssIdentifier(value, start) {
  let index = start;
  let normalized = "";
  let dynamic = false;
  let malformed = false;

  while (index < value.length) {
    if (value[index] === DYNAMIC_VALUE_MARKER) {
      dynamic = true;
      break;
    }
    if (value[index] === "\\") {
      const escape = consumeCssEscape(value, index);
      if (!escape.valid) {
        malformed = true;
        index = escape.end;
        break;
      }
      normalized += escape.value;
      index = escape.end;
      continue;
    }
    if (!isCssNameCodePoint(value, index)) break;
    const length = codePointLength(value, index);
    normalized += value.slice(index, index + length);
    index += length;
  }

  return { dynamic, end: index, malformed, value: normalized };
}

function skipCssTrivia(value, start) {
  let index = start;

  while (index < value.length) {
    if (isCssWhitespace(value, index)) {
      index = consumeCssWhitespace(value, index);
      continue;
    }
    if (value[index] === "/" && value[index + 1] === "*") {
      const end = value.indexOf("*/", index + 2);
      if (end === -1) return { end: value.length, malformed: true };
      index = end + 2;
      continue;
    }
    break;
  }

  return { end: index, malformed: false };
}

function readCssCustomProperty(value, start) {
  const trivia = skipCssTrivia(value, start);
  const identifier = consumeCssIdentifier(value, trivia.end);
  return {
    ...identifier,
    end: identifier.end,
    malformed: trivia.malformed || identifier.malformed,
    start: trivia.end,
  };
}

function isPotentialPrivateIdentityPrefix(property, form) {
  // A dynamic value in var(...) or a supported parenthesized Tailwind form can
  // resolve to any custom property, including a private Butler identity token.
  // Outside ButlerMark those constructions must fail closed even when their
  // static prefix is empty (or follows legal CSS trivia). Static semantic-role
  // properties remain allowed because this branch runs only for ambiguous
  // constructions. A named Tailwind alias is not itself a custom-property
  // construction, so retain its narrower private-namespace check.
  if (form !== "tailwind-named-alias") {
    return true;
  }
  return property === "--category-" || property === "--color-category-";
}

function privateReference(property, form, start, ambiguous = false) {
  if (PRIVATE_IDENTITY_CUSTOM_PROPERTIES.has(property)) {
    return { ambiguous, form, property, start };
  }
  if (ambiguous && isPotentialPrivateIdentityPrefix(property, form)) {
    return { ambiguous: true, form, property, start };
  }
  return null;
}

function isCssFunctionBoundary(value, index) {
  if (index === 0) return true;
  return !isCssNameCodePoint(value, index - 1) && value[index - 1] !== "\\";
}

function findCssVarReferences(value) {
  const references = [];

  for (let index = 0; index < value.length; index += 1) {
    if (!isCssFunctionBoundary(value, index)) continue;

    const functionName = consumeCssIdentifier(value, index);
    if (
      functionName.dynamic ||
      functionName.malformed ||
      functionName.value.toLowerCase() !== "var" ||
      value[functionName.end] !== "("
    ) continue;

    const property = readCssCustomProperty(value, functionName.end + 1);
    const reference = privateReference(
      property.value,
      "css-var",
      property.start,
      property.dynamic || property.malformed,
    );
    if (reference) references.push(reference);
  }

  return references;
}

function isTailwindClassBoundary(value, index) {
  if (index === 0) return true;
  const previous = value[index - 1];
  return !(
    (previous >= "a" && previous <= "z") ||
    (previous >= "A" && previous <= "Z") ||
    (previous >= "0" && previous <= "9") ||
    previous === "-" ||
    previous === "_" ||
    previous === "\\"
  );
}

function utilityAt(value, index) {
  if (!isTailwindClassBoundary(value, index)) return null;
  return TAILWIND_COLOR_UTILITIES_BY_LENGTH.find(
    (utility) =>
      value.startsWith(utility, index) && value[index + utility.length] === "-",
  );
}

function findTailwindReferences(value) {
  const references = [];

  for (let index = 0; index < value.length; index += 1) {
    const utility = utilityAt(value, index);
    if (!utility) continue;

    const valueStart = index + utility.length + 1;
    if (value[valueStart] === "(") {
      const first = readCssCustomProperty(value, valueStart + 1);
      let property = first;

      if (first.value === "color" && value[first.end] === ":") {
        property = readCssCustomProperty(value, first.end + 1);
      }

      const reference = privateReference(
        property.value,
        "tailwind-parenthesized",
        property.start,
        property.dynamic || property.malformed,
      );
      if (reference) references.push(reference);
      continue;
    }

    const alias = consumeCssIdentifier(value, valueStart);
    const property = `--${alias.value}`;
    const reference = privateReference(
      property,
      "tailwind-named-alias",
      valueStart,
      alias.dynamic || alias.malformed,
    );
    if (reference && (TAILWIND_IDENTITY_ALIASES.has(alias.value) || reference.ambiguous)) {
      references.push(reference);
    }
  }

  return references;
}

/**
 * Normalize one complete CSS custom-property identifier under CSS escape
 * rules. Null means the input is malformed, dynamic, or has trailing syntax.
 */
export function normalizeCssCustomProperty(value) {
  const identifier = consumeCssIdentifier(value, 0);
  const trailing = skipCssTrivia(value, identifier.end);
  if (
    !identifier.value ||
    identifier.dynamic ||
    identifier.malformed ||
    trailing.malformed ||
    trailing.end !== value.length
  ) {
    return null;
  }
  return identifier.value;
}

/**
 * Return canonical private identity references embedded in one static string.
 * A TemplateLiteral can join its quasis with DYNAMIC_VALUE_MARKER to make an
 * identity-shaped interpolation fail closed.
 */
export function findPrivateIdentityReferences(value) {
  const references = [...findCssVarReferences(value), ...findTailwindReferences(value)];
  const seen = new Set();

  return references
    .filter((reference) => {
      const key = `${reference.form}:${reference.start}:${reference.property}:${reference.ambiguous}`;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    })
    .sort((left, right) => left.start - right.start);
}

function maskCssCommentsAndStrings(source) {
  let masked = "";
  let index = 0;

  while (index < source.length) {
    if (source[index] === "/" && source[index + 1] === "*") {
      const end = source.indexOf("*/", index + 2);
      const commentEnd = end === -1 ? source.length : end + 2;
      for (; index < commentEnd; index += 1) {
        masked += source[index] === "\n" || source[index] === "\r" ? source[index] : " ";
      }
      continue;
    }

    if (source[index] === '"' || source[index] === "'") {
      const quote = source[index];
      masked += " ";
      index += 1;
      while (index < source.length) {
        if (source[index] === "\\") {
          masked += source[index] === "\n" || source[index] === "\r" ? source[index] : " ";
          index += 1;
          if (index < source.length) {
            masked += source[index] === "\n" || source[index] === "\r" ? source[index] : " ";
            index += 1;
          }
          continue;
        }
        const character = source[index];
        masked += character === "\n" || character === "\r" ? character : " ";
        index += 1;
        if (character === quote) break;
      }
      continue;
    }

    masked += source[index];
    index += 1;
  }

  return masked;
}

/**
 * Scan a CSS stylesheet while excluding comments and string literals, which
 * cannot consume a custom property at runtime. Raw custom-property
 * declarations remain visible but are intentionally harmless: the private
 * surface is a *reference* boundary, not a ban on defining its tokens.
 */
export function findPrivateIdentityReferencesInStylesheet(source) {
  return findPrivateIdentityReferences(maskCssCommentsAndStrings(source));
}

function cssSourcePaths(directory) {
  return readdirSync(directory, { withFileTypes: true })
    .sort((left, right) => left.name.localeCompare(right.name))
    .flatMap((entry) => {
      const path = join(directory, entry.name);
      if (entry.isDirectory()) return cssSourcePaths(path);
      return entry.isFile() && extname(entry.name) === ".css" ? [path] : [];
    });
}

function lineAndColumn(source, index) {
  const prefix = source.slice(0, index);
  const lines = prefix.split(/\r\n|\r|\n/);
  return { column: lines.at(-1).length + 1, line: lines.length };
}

function checkCssSources() {
  const scriptPath = fileURLToPath(import.meta.url);
  const frontendDirectory = dirname(dirname(scriptPath));
  const sourceDirectory = join(frontendDirectory, "src");
  let hasFailures = false;

  for (const path of cssSourcePaths(sourceDirectory)) {
    const source = readFileSync(path, "utf8");
    for (const reference of findPrivateIdentityReferencesInStylesheet(source)) {
      const { line, column } = lineAndColumn(source, reference.start);
      console.error(
        `${relative(frontendDirectory, path)}:${line}:${column}: Butler identity token ${reference.property} is private to ButlerMark; use a typed semantic role helper instead.`,
      );
      hasFailures = true;
    }
  }

  if (hasFailures) process.exitCode = 1;
}

const scriptPath = fileURLToPath(import.meta.url);
const runningAsScript = process.argv[1] && resolve(process.argv[1]) === scriptPath;
if (runningAsScript) {
  if (process.argv.length !== 3 || process.argv[2] !== "--check") {
    console.error("Usage: node scripts/visual-role-css-guard.mjs --check");
    process.exitCode = 2;
  } else {
    checkCssSources();
  }
}
