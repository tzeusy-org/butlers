#!/usr/bin/env node
/** Generate the wire contract from the real static FastAPI application. */
import { execFileSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import openapiTS, { astToString } from "openapi-typescript";

const root = fileURLToPath(new URL("../../", import.meta.url));
const output = fileURLToPath(new URL("../src/api/schema.gen.ts", import.meta.url));
const args = process.argv.slice(2);
if (args.some((arg) => arg !== "--check")) {
  throw new Error("Usage: generate-api-types.mjs [--check]");
}
const schema = JSON.parse(execFileSync(`${root}.venv/bin/python`, [
  `${root}scripts/export_openapi.py`,
], { cwd: root, encoding: "utf8", timeout: 120_000, maxBuffer: 32 * 1024 * 1024 }));
const source = astToString(await openapiTS(schema, { alphabetize: true, defaultNonNullable: false }));
if (args.includes("--check")) {
  if (readFileSync(output, "utf8") !== source) {
    console.error("Generated API contract is stale. Run npm run generate:api in frontend/.");
    process.exitCode = 1;
  }
} else {
  writeFileSync(output, source);
}
