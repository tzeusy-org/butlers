/** Adversarial asynchronous bookkeeping checks for the existing CI contract node.
 * This is a controlled caller, not an installed-framework/corpus receipt.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const root = process.cwd();
const { default: Reporter } = await import(pathToFileURL(
  path.join(root, 'scripts/ci_vitest_reporter.mjs')));
process.chdir(path.join(root, 'frontend'));
const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'vitest-protocol-'));
const output = path.join(directory, 'execution.json');
process.env.BUTLERS_VITEST_EVIDENCE = output;
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function setup() {
  const reporter = new Reporter();
  const test = {
    id: 'synthetic-current-occurrence', fullName: 'synthetic occurrence',
    options: { mode: 'run' }, result: () => ({ state: 'passed' }),
  };
  const module = {
    moduleId: path.join(root, 'frontend/src/pages/SettingsModelsPage.test.tsx'),
    children: { allTests: () => [test] }, errors: () => [],
    ok: () => true, state: () => 'passed',
    diagnostic: () => ({ environmentSetupDuration: 0, prepareDuration: 0, collectDuration: 0, setupDuration: 0, duration: 0 }),
  };
  test.module = module;
  const calls = [];
  const values = { ready: {}, result: {} };
  const run = {
    async updated(batch, marker) {
      assert.equal(this, run);
      assert.equal(marker, values);
      calls.push(batch.kind);
      if (batch.kind === 'ready') {
        await delay(30);
        reporter.onTestCaseReady(test);
        return values.ready;
      }
      if (batch.kind === 'result') {
        reporter.onTestCaseResult(test);
        return values.result;
      }
      if (batch.kind === 'reject') throw new TypeError('synthetic private content');
      return marker;
    },
  };
  reporter.onInit({
    version: '3.2.4', _testRun: run,
    config: {
      pool: 'forks', isolate: true, watch: false, sequence: {},
      fileParallelism: true, testTimeout: 5000, hookTimeout: 10000, retry: 0,
    },
  });
  reporter.onTestModuleQueued(module);
  reporter.onTestModuleCollected(module);
  reporter.onTestModuleStart(module);
  return { reporter, module, run, calls, values };
}

try {
  {
    const s = setup();
    const ready = s.run.updated({ kind: 'ready' }, s.values);
    const result = s.run.updated({ kind: 'result' }, s.values);
    const [a, b] = await Promise.all([ready, result]);
    assert.equal(a, s.values.ready);
    assert.equal(b, s.values.result);
    assert.deepEqual(s.calls, ['ready', 'result']);
    assert.deepEqual(s.reporter.problems, {});
    s.reporter.onTestModuleEnd(s.module);
    await s.reporter.onTestRunEnd([s.module], [], 'passed');
    assert.equal(JSON.parse(fs.readFileSync(output)).update_errors, 0);
    await assert.rejects(s.run.updated({ kind: 'result' }, s.values));
    assert.equal(s.reporter.updateQueue.failures, 1);
    assert.equal(process.exitCode, 1);
    process.exitCode = 0;
  }
  {
    const s = setup();
    await assert.rejects(s.run.updated({ kind: 'reject' }, s.values), TypeError);
    assert.equal(await s.run.updated({ kind: 'ordinary' }, s.values), s.values);
    assert.equal(s.reporter.updateQueue.failures, 1);
    await s.reporter.onTestRunEnd([], [], 'passed');
    assert.equal(JSON.parse(fs.readFileSync(output)).update_errors, 1);
    assert.equal(process.exitCode, 1);
    process.exitCode = 0;
  }
  {
    const s = setup();
    await assert.rejects(s.run.updated({ kind: 'result' }, s.values));
    assert.equal(s.reporter.updateQueue.failures, 1);
    assert.deepEqual(s.reporter.problems, { ready_multiplicity: 1 });
  }
  {
    const s = setup();
    await s.run.updated({ kind: 'ready' }, s.values);
    await s.run.updated({ kind: 'ready' }, s.values);
    await assert.rejects(s.run.updated({ kind: 'result' }, s.values));
    assert.deepEqual(s.reporter.problems, { ready_multiplicity: 1 });
  }
  {
    const s = setup();
    await s.run.updated({ kind: 'ready' }, s.values);
    await s.run.updated({ kind: 'result' }, s.values);
    await assert.rejects(s.run.updated({ kind: 'result' }, s.values));
    assert.deepEqual(s.reporter.problems, { result_duplicate: 1 });
  }
  {
    const s = setup();
    const ready = s.run.updated({ kind: 'ready' }, s.values);
    const ending = s.reporter.onTestRunEnd([], [], 'passed');
    await delay(5);
    const result = s.run.updated({ kind: 'result' }, s.values);
    await Promise.all([ready, result, ending]);
    assert.equal(Object.keys(JSON.parse(fs.readFileSync(output)).results).length, 1);
    assert.equal(s.reporter.updateQueue.ended, true);
    assert.equal(s.reporter.updateQueue.failures, 0);
  }
  console.log('ordered-vitest-bookkeeping-conformance-passed');
} finally {
  fs.rmSync(directory, { recursive: true, force: true });
}
