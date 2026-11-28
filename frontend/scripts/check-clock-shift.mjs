#!/usr/bin/env node
// Proves the vitest clock-shift guard still detects an unpinned date test (bu-5ii4gj.22).
// The decoy must pass with the real clock and fail with VITEST_CLOCK_SHIFT_DAYS=60.
import { spawnSync } from "node:child_process";

const DECOY = "src/test/clock-shift-decoy.test.ts";

function run(shiftDays) {
  const env = { ...process.env, CLOCK_SHIFT_DECOY: "1" };
  if (shiftDays) env.VITEST_CLOCK_SHIFT_DAYS = String(shiftDays);
  else delete env.VITEST_CLOCK_SHIFT_DAYS;
  const result = spawnSync(
    "npx",
    ["vitest", "run", "--configLoader", "runner", DECOY],
    { env, encoding: "utf8" },
  );
  return { status: result.status, output: `${result.stdout}${result.stderr}` };
}

const real = run(0);
if (real.status !== 0) {
  console.error("clock-shift guard: the decoy must pass with the real clock\n" + real.output);
  process.exit(1);
}
const shifted = run(60);
if (shifted.status === 0) {
  console.error("clock-shift guard: the decoy still passed at +60d, so the shift is not applied");
  process.exit(1);
}
console.log("clock-shift guard: decoy passes unshifted and fails at +60d");
