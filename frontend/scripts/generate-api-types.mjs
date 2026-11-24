import { execFileSync } from "node:child_process";
import {
  existsSync, mkdirSync, mkdtempSync, readFileSync, renameSync, rmSync, writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import openapiTS, { astToString } from "openapi-typescript";

const root = fileURLToPath(new URL("../../", import.meta.url));
const output = join(root, "docs/api_and_protocols/generated/dashboard-openapi-schema.gen.ts");
const regenerate = "npm --prefix frontend run generate:api";
const args = process.argv.slice(2);
const check = args.length === 1 && args[0] === "--check";

if (args.length && !check) {
  throw new Error("Usage: generate-api-types.mjs [--check]");
}

const isolated = mkdtempSync(join(tmpdir(), "butlers-openapi-"));
let raw;
try {
  raw = execFileSync(join(root, ".venv/bin/python"), [
    "-I", "-B", "-X", "utf8", join(root, "scripts/export_openapi.py"),
  ], {
    cwd: isolated,
    env: { HOME: isolated, PATH: "/usr/bin:/bin", TZ: "UTC" },
    encoding: "utf8",
    timeout: 120_000,
    maxBuffer: 32 * 1024 * 1024,
  });
} finally {
  rmSync(isolated, { recursive: true, force: true });
}

const schema = JSON.parse(raw, (key, value) => {
  if (key === "$ref" && typeof value === "string" && !value.startsWith("#/")) {
    throw new Error("The dashboard OpenAPI schema must use local references.");
  }
  return value;
});
const generated = Buffer.from(astToString(await openapiTS(schema, {
  alphabetize: true,
  defaultNonNullable: false,
})));

if (check) {
  if (!existsSync(output) || !readFileSync(output).equals(generated)) {
    console.error(`Dashboard OpenAPI types are stale. Run ${regenerate}.`);
    process.exitCode = 1;
  }
} else {
  mkdirSync(dirname(output), { recursive: true });
  const staging = mkdtempSync(join(dirname(output), ".openapi-"));
  try {
    const staged = join(staging, "schema.gen.ts");
    writeFileSync(staged, generated);
    renameSync(staged, output);
  } finally {
    rmSync(staging, { recursive: true, force: true });
  }
}
