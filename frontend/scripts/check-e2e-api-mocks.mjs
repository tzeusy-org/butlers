import { readFileSync, mkdirSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";

// Two consumed mutations have no concrete TypeScript response model. Project
// their existing Pydantic models, never the void/unknown client return type.
// This deliberately supports only these flat, required primitive fields.
const contracts = [
  ["putPermission", "permissions", "/{butler}/{perm}", "PermissionSetResponse"],
  ["updateSpendCeiling", "spend", "/ceiling", "SpendCeiling"],
];
// Python's stdlib AST distinguishes executable declarations from inert text.
// The helper parses only source text and supports only the two contracts above.
export function projectModel(source, route, model) {
  const result = spawnSync("python3", [fileURLToPath(new URL("./e2e_backend_response_models.py", import.meta.url))], {
    input: JSON.stringify({ source, route, model }), encoding: "utf8", timeout: 10000,
  });
  if (result.error || result.status !== 0) {
    throw new Error(result.stderr || `Unsupported backend response contract: ${result.error?.message}`);
  }
  return JSON.parse(result.stdout);
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
