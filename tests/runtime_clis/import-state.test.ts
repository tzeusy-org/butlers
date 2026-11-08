import { test } from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";

function importProbe(url: URL, unchanged: boolean) {
  const code = `const assert=require('node:assert/strict');const key=Symbol.for('undici.globalDispatcher.1');const before=globalThis[key];import(${JSON.stringify(url.href)}).then(()=>assert.${unchanged ? "strictEqual" : "notStrictEqual"}(globalThis[key],before)).catch(error=>{console.error(error.message);process.exitCode=1;});`;
  const args = [
    ...(process.versions.bun ? [] : ["--experimental-strip-types"]),
    "--eval",
    code,
  ];
  const result = spawnSync(process.execPath, args, {
    encoding: "utf8",
    timeout: 5000,
    env: {},
  });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.signal, null);
}

test("selected executor import leaves the original global dispatcher unchanged", () => {
  importProbe(
    new URL("../../deploy/runtime-clis/shared/egress.ts", import.meta.url),
    true,
  );
});

test("fresh-process control detects the upstream package-root dispatcher side effect", () => {
  importProbe(
    new URL(
      "../../deploy/runtime-clis/shared/node_modules/butlers-egress-undici/index.js",
      import.meta.url,
    ),
    false,
  );
});
