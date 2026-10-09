/** Locked Vitest collection/occurrence evidence; raw task text stays in memory. */
import { createHash } from 'node:crypto';
import fs from 'node:fs';
import { availableParallelism } from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

function fail() {
  throw new Error('locked Vitest occurrence evidence invalid');
}

function ascii(value) {
  return JSON.stringify(value).replace(/[\u007f-\uffff]/g, (c) =>
    `\\u${c.charCodeAt(0).toString(16).padStart(4, '0')}`);
}

function opaque(file, name) {
  // Match Python json.dumps([file, name]), including Unicode and separators.
  return createHash('sha256').update(`[${ascii(file)}, ${ascii(name)}]`).digest('hex');
}

function sourcePath(moduleId, root) {
  const resolved = fs.realpathSync(moduleId);
  const relative = path.relative(root, resolved).replaceAll(path.sep, '/');
  if (!relative.startsWith('frontend/') || relative.includes('/../') ||
      relative.startsWith('../') || !fs.statSync(resolved).isFile()) fail();
  return relative;
}

function configEvidence(ctx) {
  const config = ctx.config;
  if (config.pool !== 'forks' || config.isolate !== true || config.watch !== false ||
      config.testNamePattern || config.changed || config.related ||
      config.sequence.shuffle || config.sequence.concurrent) fail();
  return {
    node: process.versions.node,
    vitest: ctx.version,
    pool: config.pool,
    isolate: config.isolate,
    available_parallelism: availableParallelism(),
    max_workers: config.maxWorkers ?? null,
    min_workers: config.minWorkers ?? null,
    file_parallelism: config.fileParallelism,
    test_timeout: config.testTimeout,
    hook_timeout: config.hookTimeout,
    retry: config.retry ?? 0,
    sequence_shuffle: Boolean(config.sequence.shuffle),
  };
}

function snapshot(module, root) {
  const file = sourcePath(module.moduleId, root);
  const items = {};
  for (const test of module.children.allTests()) {
    const mode = test.options.mode;
    if (!['run', 'skip', 'todo', 'only'].includes(mode) ||
        typeof test.id !== 'string' || typeof test.fullName !== 'string') fail();
    const token = opaque(file, test.id);
    if (Object.hasOwn(items, token)) fail();
    items[token] = { key: opaque(file, test.fullName), mode };
  }
  return { file, items, errors: module.errors().length, ok: module.ok() };
}

function moduleMetrics(module, root) {
  const diagnostic = module.diagnostic();
  const values = {};
  for (const name of ['environmentSetupDuration', 'prepareDuration', 'collectDuration', 'setupDuration', 'duration']) {
    const value = diagnostic[name];
    if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) fail();
    values[name] = value;
  }
  return [sourcePath(module.moduleId, root), values];
}

function publish(output, value) {
  const temporary = output + '.partial';
  fs.writeFileSync(temporary, JSON.stringify(value));
  fs.renameSync(temporary, output);
}

/** Actual unsharded collection through the same pinned public installed API. */
async function collect(root, output) {
  process.env.TEST = 'true';
  process.env.VITEST = 'true';
  process.env.NODE_ENV ??= 'test';
  const frontend = path.join(root, 'frontend');
  const { createVitest, BaseSequencer } = await import(pathToFileURL(
    path.join(frontend, 'node_modules/vitest/dist/node.js')));
  let ctx;
  try {
    ctx = await createVitest('test', {
      run: true, watch: false, configLoader: 'runner', reporters: [],
    });
    const config = configEvidence(ctx);
    if (ctx.config.sequence.sequencer !== BaseSequencer) fail();
    const specs = await ctx.getRelevantTestSpecifications([]);
    if (!specs.length || specs.some((s) => s.project.name !== '' ||
        s.pool !== 'forks' || (s.project.config.sequence.groupOrder ?? 0) !== 0)) fail();
    const files = specs.map((s) => sourcePath(s.moduleId, root));
    if (new Set(files).size !== files.length) fail();
    const halves = {};
    for (const index of [1, 2]) {
      const sequencer = new BaseSequencer({
        config: { ...ctx.config, shard: { index, count: 2 } },
      });
      halves[index] = (await sequencer.shard(specs))
        .map((s) => sourcePath(s.moduleId, root)).sort();
    }
    const { testModules, unhandledErrors } = await ctx.collect([]);
    const modules = {};
    for (const module of testModules) {
      const value = snapshot(module, root);
      if (Object.hasOwn(modules, value.file)) fail();
      modules[value.file] = value;
    }
    publish(output, {
      schema: 'ci-vitest-reference.v2',
      config, files: files.sort(), halves, modules,
      file_metrics: Object.fromEntries(testModules.map((m) => moduleMetrics(m, root))),
      unhandled_errors: unhandledErrors.length,
      controller_exit: process.exitCode ?? 0,
      complete: true,
    });
  } finally {
    await ctx?.close();
  }
}

/** Observe actual CLI execution without altering workers, runner or test bodies. */
export default class OccurrenceReporter {
  constructor() {
    this.root = path.resolve(process.cwd(), '..');
    this.output = process.env.BUTLERS_VITEST_EVIDENCE;
    this.modules = {};
    this.queued = {};
    this.ready = {};
    this.results = {};
    this.starts = {};
    this.ends = {};
    this.problems = {};
  }

  onInit(ctx) {
    this.config = configEvidence(ctx);
    if (!this.output || ctx.version !== '3.2.4' ||
        typeof ctx._testRun?.updated !== 'function' || this.updateQueue) fail();
    const run = ctx._testRun;
    const original = run.updated;
    const queue = { tail: Promise.resolve(), failures: 0, ended: false };
    this.updateQueue = queue;
    // The stock RPC receiver processes batches concurrently. Serialize only
    // metadata delivery in arrival order; preserve receiver, return and reject.
    run.updated = function (...args) {
      if (queue.ended) {
        queue.failures += 1;
        process.exitCode = 1;
        return Promise.reject(new Error('late Vitest metadata update refused'));
      }
      const receiver = this;
      const result = queue.tail.then(() => Reflect.apply(original, receiver, args));
      // This internal drain continues after failure, while the ORIGINAL caller
      // receives the rejecting result. Terminal proof separately rejects every
      // recorded failure; this handler never turns a rejection into readiness.
      queue.tail = result.then(() => undefined, () => { queue.failures += 1; });
      return result;
    };
  }

  onTestModuleQueued(module) {
    const file = sourcePath(module.moduleId, this.root);
    this.queued[file] = (this.queued[file] ?? 0) + 1;
  }

  onTestModuleCollected(module) {
    // This snapshots the pre-body-emitted RPC tree synchronously. The worker
    // does not await controller acknowledgement: no body barrier is claimed.
    const value = snapshot(module, this.root);
    if (Object.hasOwn(this.modules, value.file)) fail();
    this.modules[value.file] = value;
  }

  onTestModuleStart(module) {
    const file = sourcePath(module.moduleId, this.root);
    if (!Object.hasOwn(this.modules, file)) fail();
    this.starts[file] = (this.starts[file] ?? 0) + 1;
  }

  occurrence(test) {
    const file = sourcePath(test.module.moduleId, this.root);
    const token = opaque(file, test.id);
    const declaration = this.modules[file]?.items[token];
    if (!declaration) { this.problem("unknown_occurrence"); fail(); }
    if (declaration.key !== opaque(file, test.fullName)) {
      this.problem("changed_identity"); fail();
    }
    return token;
  }

  onTestCaseReady(test) {
    const token = this.occurrence(test);
    this.ready[token] = (this.ready[token] ?? 0) + 1;
  }

  problem(kind) {
    this.problems[kind] = (this.problems[kind] ?? 0) + 1;
  }

  onTestCaseResult(test) {
    const token = this.occurrence(test);
    if (this.ready[token] !== 1) { this.problem("ready_multiplicity"); fail(); }
    if (Object.hasOwn(this.results, token)) { this.problem("result_duplicate"); fail(); }
    const state = test.result().state;
    if (!['passed', 'skipped', 'failed'].includes(state)) {
      this.problem(state === 'pending' ? 'result_pending' : 'result_unknown'); fail();
    }
    this.results[token] = { state, declared_mode: test.options.mode };
  }

  onTestModuleEnd(module) {
    const value = snapshot(module, this.root);
    const before = this.modules[value.file];
    if (!before || JSON.stringify(value.items) !== JSON.stringify(before.items)) fail();
    this.ends[value.file] = { count: (this.ends[value.file]?.count ?? 0) + 1,
      errors: value.errors, ok: value.ok, state: module.state() };
  }

  async onTestRunEnd(modules, errors, reason) {
    // Updates may arrive while an earlier async reporter callback drains. The
    // equality check and terminal latch have no intervening await.
    for (;;) {
      const tail = this.updateQueue.tail;
      await tail;
      if (tail === this.updateQueue.tail) {
        this.updateQueue.ended = true;
        break;
      }
    }
    if (this.updateQueue.failures) process.exitCode = 1;
    publish(this.output, {
      schema: 'ci-vitest-execution.v2', config: this.config,
      modules: this.modules, queued: this.queued, starts: this.starts,
      ends: this.ends, ready: this.ready, results: this.results,
      terminal_files: modules.map((m) => sourcePath(m.moduleId, this.root)).sort(),
      file_metrics: Object.fromEntries(modules.map((m) => moduleMetrics(m, this.root))),
      unhandled_errors: errors.length, reporter_problems: this.problems,
      update_errors: this.updateQueue.failures, reason, complete: true,
    });
  }

  onUserConsoleLog() {
    return false;
  }
}

if (process.argv[2] === 'collect' &&
    path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    await collect(path.resolve(process.argv[3]), process.argv[4]);
  } catch {
    // Fixed classification only; no exception/provider/task text is printed.
    process.stderr.write('locked-vitest-reference-unavailable\n');
    process.exitCode = 2;
  }
}
