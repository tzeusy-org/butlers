import { readFileSync, mkdirSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

// Two consumed mutations have no concrete TypeScript response model. Project
// their existing Pydantic models, never the void/unknown client return type.
// This deliberately supports only these flat, required primitive fields.
const contracts = [
  ["putPermission", "permissions", "/{butler}/{perm}", "PermissionSetResponse"],
  ["updateSpendCeiling", "spend", "/ceiling", "SpendCeiling"],
];
const primitives = { str: "string", datetime: "string", float: "number", bool: "boolean" };

export function projectModel(source, route, model) {
  const decorator = `@router.put("${route}", response_model=ApiResponse[${model}])`;
  if (!source.includes(decorator)) throw new Error(`Missing exact response_model binding: ${model}`);
  const lines = source.split("\n");
  const declarations = lines.filter(line => new RegExp(`^(?:class ${model}\\b|${model}\\s*=)`).test(line));
  const start = lines.indexOf(`class ${model}(BaseModel):`);
  // Track statement starts, so a multiline decorator (including intervening
  // comments) cannot hide behind its closing parenthesis.
  const previousStatement = lines.slice(0, start).findLast(line => /^(?:@|(?:async )?def |class |from |import |\w+\s*=)/.test(line));
  if (declarations.length !== 1 || start < 0 || previousStatement?.startsWith("@")) {
    throw new Error(`Unsupported model declaration: ${model}`);
  }
  const fields = [];
  let inDocstring = false;
  for (const line of lines.slice(start + 1)) {
    if (line && !line.startsWith(" ")) break;
    if (!line.trim() || line.trim().startsWith("#")) continue;
    if (line.trim().startsWith('"""')) {
      if (!line.trim().slice(3).includes('"""')) inDocstring = !inDocstring;
      continue;
    }
    if (inDocstring) continue;
    const field = /^    (\w+): (str|datetime|float|bool)$/.exec(line);
    if (!field || fields.some(([name]) => name === field[1])) {
      throw new Error(`Unsupported field/default/union/validator in ${model}: ${line.trim()}`);
    }
    fields.push([field[1], primitives[field[2]]]);
  }
  if (!fields.length || inDocstring) throw new Error(`Incomplete model: ${model}`);
  return fields.map(([name, type]) => `${name}: ${type};`).join(" ");
}

export function generate() {
  const root = new URL("../../", import.meta.url);
  const entries = contracts.map(([key, module, route, model]) => {
    const source = readFileSync(new URL(`src/butlers/api/routers/${module}.py`, root), "utf8");
    return `  ${key}: ApiResponse<{ ${projectModel(source, route, model)} }>;`;
  });
  const output = new URL("../.vite/e2e-backend-responses.d.ts", import.meta.url);
  mkdirSync(new URL("../.vite/", import.meta.url), { recursive: true });
  writeFileSync(output, 'import type { ApiResponse } from "../src/api/types";\n' +
    'export interface BackendResponses {\n' + entries.join("\n") + '\n}\n');
}

if (process.argv[1] === fileURLToPath(import.meta.url)) generate();
