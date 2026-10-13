'use strict';

// No SDK or subject error text crosses the fixed IPC channel.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const {spawn, fork} = require('node:child_process');
const {performance} = require('node:perf_hooks');

const SCHEDULE = Object.freeze([0, 120, 300, 600, 900, 1200, 1440, 1560]);
const SDK_KEYS = Object.freeze(['ACTIONS_RUNTIME_TOKEN', 'ACTIONS_RESULTS_URL',
  'ACTIONS_RUNTIME_URL', 'ACTIONS_CACHE_URL']);
const UPLOAD_KEYS = Object.freeze([...SDK_KEYS, 'GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT',
  'GITHUB_REPOSITORY', 'GITHUB_SERVER_URL', 'GITHUB_WORKSPACE', 'RUNNER_TRACKING_ID',
  'PATH', 'SYSTEMROOT', 'TMPDIR', 'TEMP', 'TMP', 'RUNNER_TEMP']);
const RESULT_KEYS = ['code', 'id', 'digest', 'size'];
const MAX_BYTES = 16 * 1024 * 1024;
const MAX_PROCESSES = 256;

function subjectEnvironment(environment) {
  return Object.fromEntries(Object.entries(environment).filter(([key]) => !SDK_KEYS.includes(key)));
}
function uploaderEnvironment(environment) {
  const value = Object.fromEntries(UPLOAD_KEYS.filter(key => Object.hasOwn(environment, key))
    .map(key => [key, environment[key]]));
  value.WATCHDOG_UPLOADER = '1';
  value.ACTIONS_ARTIFACT_UPLOAD_CONCURRENCY = '1';
  return value;
}
function finalized(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value) ||
      Object.keys(value).sort().join() !== RESULT_KEYS.sort().join() ||
      !['finalized', 'failed', 'unavailable'].includes(value.code)) return false;
  if (value.code !== 'finalized') return value.id === null && value.digest === null && value.size === null;
  return Number.isSafeInteger(value.id) && value.id > 0 && Number.isSafeInteger(value.size) &&
    value.size >= 0 && typeof value.digest === 'string' && /^[a-f0-9]{64}$/.test(value.digest);
}
function unknown(code = 'failed') { return {code, id: null, digest: null, size: null}; }
function fileBytes(file) {
  const fd = fs.openSync(file, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW | fs.constants.O_NONBLOCK);
  try {
    const stat = fs.fstatSync(fd);
    if (!stat.isFile() || stat.size > MAX_BYTES) throw new Error('sealed-file-refused');
    return fs.readFileSync(fd);
  } finally { fs.closeSync(fd); }
}
function sealedFiles(directory) {
  if (fs.lstatSync(directory).isSymbolicLink() ||
      fs.readdirSync(directory).sort().join() !== 'manifest.json,snapshot.json') throw new Error('seal-refused');
  const manifestFile = path.join(directory, 'manifest.json');
  const snapshotFile = path.join(directory, 'snapshot.json');
  const manifest = JSON.parse(fileBytes(manifestFile));
  const snapshot = fileBytes(snapshotFile);
  const record = manifest.files?.['snapshot.json'];
  if (!record || manifest.complete !== false || manifest.schema !== 'ci-watchdog-manifest.v1' ||
      Object.keys(manifest.files).join() !== 'snapshot.json' || record.bytes !== snapshot.length ||
      record.sha256 !== crypto.createHash('sha256').update(snapshot).digest('hex')) throw new Error('seal-refused');
  return [manifestFile, snapshotFile];
}
async function uploadWithClient(client, message) {
  try {
    if (!message || Object.keys(message).sort().join() !== 'directory,name' ||
        !/^ci-watchdog-unit-1-[1-9][0-9]*-[1-9][0-9]*-[a-f0-9]{12}-[0-7]$/.test(message.name)) return unknown();
    const files = sealedFiles(message.directory);
    const result = await client.uploadArtifact(message.name, files, message.directory,
      {retentionDays: 14, compressionLevel: 0});
    const output = {code: 'finalized', id: result.id, digest: result.digest, size: result.size};
    return finalized(output) ? output : unknown();
  } catch (_) { return unknown(); }
}
function numericProcess(pid) {
  try {
    const raw = fs.readFileSync(`/proc/${pid}/stat`, 'utf8');
    const data = raw.slice(raw.lastIndexOf(')') + 2).trim().split(/\s+/);
    const result = {pid, ppid: Number(data[1]), start_ticks: Number(data[19]),
      state: /^[RSDZTWIPX]$/.test(data[0]) ? data[0] : '?', threads: Number(data[17]),
      cpu_ticks: Number(data[11]) + Number(data[12]), rss_pages: Math.max(0, Number(data[21]))};
    if (Object.entries(result).some(([key, value]) => key !== 'state' &&
        (!Number.isSafeInteger(value) || value < 0))) return null;
    return result;
  } catch (_) { return null; }
}
function descendantSamples(rootPid) {
  const todo = [rootPid], result = [], seen = new Set();
  let truncated = false;
  while (todo.length && result.length < MAX_PROCESSES) {
    const pid = todo.shift();
    if (seen.has(pid)) continue;
    seen.add(pid);
    const sample = numericProcess(pid);
    if (!sample) continue;
    result.push(sample);
    try {
      // Only numeric child PIDs: never argv, environ, fd paths or process names.
      const tasks = fs.readdirSync(`/proc/${pid}/task`).filter(x => /^[0-9]+$/.test(x)).slice(0, 256);
      for (const task of tasks) {
        const children = fs.readFileSync(`/proc/${pid}/task/${task}/children`, 'utf8').trim();
        for (const child of children.split(/\s+/).filter(x => /^[0-9]+$/.test(x))) { if (todo.length < MAX_PROCESSES) todo.push(Number(child)); else truncated = true; }
      }
    } catch (_) {}
  }
  return {samples: result, truncated: truncated || todo.length > 0};
}
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
function managed(command, args, options) {
  const child = spawn(command, args, {...options, detached: true, stdio: ['ignore', 'ignore', 'ignore']});
  const owned = new Map();
  let exited = false, overflow = false;
  const collect = () => {
    for (const [pid, start] of owned) { const current = numericProcess(pid);
      if (!current || current.start_ticks !== start || ["Z", "X"].includes(current.state)) owned.delete(pid); }
    const observation = descendantSamples(child.pid); overflow ||= observation.truncated;
    for (const sample of observation.samples) { if (owned.size < MAX_PROCESSES || owned.has(sample.pid)) owned.set(sample.pid, sample.start_ticks); else overflow = true; }
  };
  collect();
  const timer = setInterval(collect, 20);
  const done = new Promise(resolve => {
    child.once('error', () => { exited = true; resolve({code: null, signal: null}); });
    child.once('exit', (code, signal) => { exited = true; resolve({code, signal}); });
  });
  return {child, owned, done, collect, timer, isExited: () => exited, overflow: () => overflow};
}
function liveOwned(handle) {
  handle.collect();
  return [...handle.owned].filter(([pid, start]) => {
    const current = numericProcess(pid);
    return current && current.start_ticks === start && !['Z', 'X'].includes(current.state);
  }).map(([pid]) => pid);
}
async function cleanup(handle, milliseconds = 2000) {
  const deadline = performance.now() + milliseconds;
  handle.collect();
  // Group only belongs to a child launched here; start-tick checks protect remembered PIDs.
  const root = numericProcess(handle.child.pid);
  const groupOwned = root && handle.owned.get(root.pid) === root.start_ticks;
  if (groupOwned) { try { process.kill(-handle.child.pid, 'SIGTERM'); } catch (_) {} }
  for (const pid of liveOwned(handle)) { try { process.kill(pid, 'SIGTERM'); } catch (_) {} }
  while (performance.now() < deadline - Math.min(500, milliseconds / 2) && liveOwned(handle).length) await delay(20);
  if (groupOwned) { const current = numericProcess(handle.child.pid);
    if (current && current.start_ticks === root.start_ticks) { try { process.kill(-handle.child.pid, 'SIGKILL'); } catch (_) {} } }
  for (const pid of liveOwned(handle)) { try { process.kill(pid, 'SIGKILL'); } catch (_) {} }
  while (performance.now() < deadline && (liveOwned(handle).length || !handle.isExited())) await delay(20);
  clearInterval(handle.timer);
  return liveOwned(handle).length === 0 && handle.isExited() && !handle.overflow?.();
}
async function boundedUpload(message, environment, milliseconds = 60000, entry = __filename, abortSignal = null) {
  const deadline = performance.now() + milliseconds;
  const cleanupReserve = Math.min(2000, milliseconds / 2);
  const child = fork(entry, [], {env: uploaderEnvironment(environment), detached: true,
    stdio: ['ignore', 'ignore', 'ignore', 'ipc']});
  const handle = {child, owned: new Map(), collect() {
    for (const [pid, start] of this.owned) { const current = numericProcess(pid);
      if (!current || current.start_ticks !== start || ["Z", "X"].includes(current.state)) this.owned.delete(pid); }
    for (const sample of descendantSamples(child.pid).samples) { if (this.owned.size < MAX_PROCESSES || this.owned.has(sample.pid)) this.owned.set(sample.pid, sample.start_ticks); }
  }, isExited: () => exited};
  let exited = false, output = unknown(), messages = 0;
  handle.timer = setInterval(() => handle.collect(), 20);
  handle.collect();
  const done = new Promise(resolve => {
    child.once('error', () => { exited = true; resolve(); });
    child.once('exit', () => { exited = true; resolve(); });
    child.on('message', value => {
      messages++;
      output = messages === 1 && finalized(value) ? value : unknown();
    });
  });
  try { child.send(message, () => {}); } catch (_) {}
  let timeout;
  let onAbort;
  const abort = new Promise(resolve => { onAbort = resolve; if (abortSignal?.aborted) resolve(); else abortSignal?.addEventListener("abort", resolve, {once: true}); });
  await Promise.race([done, abort, new Promise(resolve => { timeout = setTimeout(resolve, Math.max(0, deadline - performance.now() - cleanupReserve)); })]);
  abortSignal?.removeEventListener("abort", onAbort);
  clearTimeout(timeout);
  const completed = await cleanup(handle, Math.max(20, Math.min(2000, deadline - performance.now())));
  return {result: completed && messages === 1 ? output : unknown(), cleanup_complete: completed};
}
function writeAtomic(file, value) {
  const temporary = file + '.staging';
  fs.writeFileSync(temporary, JSON.stringify(value), {flag: 'wx', mode: 0o600});
  fs.renameSync(temporary, file);
}
async function python(command, args, environment, cwd, abortSignal = null) {
  const handle = managed(command, args, {env: environment, cwd});
  let abortHandler;
  const abort = new Promise(resolve => { abortHandler = () => resolve({code: null});
    if (abortSignal?.aborted) abortHandler(); else abortSignal?.addEventListener("abort", abortHandler, {once:true}); });
  let timeout;
  const outcome = await Promise.race([handle.done, abort, new Promise(resolve => { timeout = setTimeout(() => resolve({code:null}), 8000); })]);
  clearTimeout(timeout);
  abortSignal?.removeEventListener("abort", abortHandler);
  const complete = await cleanup(handle);
  return outcome.code === 0 && complete;
}
async function runAction() {
  const capture = path.resolve(process.env.INPUT_CAPTURE || 'capture');
  const subject = path.resolve(process.env.INPUT_SUBJECT || 'subject');
  const inventory = path.resolve(process.env.INPUT_INVENTORY);
  const directory = path.resolve(process.env.INPUT_OUTPUT);
  const binding = JSON.parse(fileBytes(path.join(inventory, 'binding.json')));
  if (binding.profile !== 'original-pr4484-unit1' || binding.run !== process.env.GITHUB_RUN_ID ||
      binding.attempt !== process.env.GITHUB_RUN_ATTEMPT || binding.repository !== 'tzeusy-org/butlers' ||
      binding.workflow !== 'CI Shard Watchdog Diagnostic' || binding.provider_head !== process.env.GITHUB_SHA) throw new Error('identity-refused');
  const executable = path.join(subject, '.venv/bin/python');
  const observer = path.join(capture, 'scripts/ci_watchdog_diagnostics.py');
  const environment = subjectEnvironment(process.env);
  environment.GITHUB_SHA = binding.subject_head;
  environment.CI_COVERAGE = '0';
  environment.CI_UNIT_WORKERS = '3';
  environment.PYTHONPATH = [path.join(capture, 'scripts'), environment.PYTHONPATH].filter(Boolean).join(path.delimiter);
  if (!await python(executable, [observer, 'initialize', '--subject', subject, '--capture', capture,
      '--inventory', inventory, '--output', directory], environment, subject)) throw new Error('initialization-refused');
  environment.CI_WATCHDOG_CONFIG = path.join(directory, 'config.json');
  environment.PYTEST_PLUGINS = [environment.PYTEST_PLUGINS, 'ci_watchdog_diagnostics'].filter(Boolean).join(',');
  const helperAbort = new AbortController();
  const started = performance.now();
  const subjectChild = managed('uv', ['run', 'python', 'scripts/check_ci_test_shards.py', 'run',
    '--lane', 'unit', '--shard', '1', '--inventory-dir', inventory], {cwd: subject, env: environment});
  const transports = SCHEDULE.map((time, sequence) => ({sequence, scheduled_s: time, transport: 'pending',
    artifact_id: null, digest: null, size: null}));
  const transportFile = path.join(directory, 'transport.json');
  const samplesFile = path.join(directory, 'samples.json');
  const sampleTimer = setInterval(() => {
    try { writeAtomic(samplesFile, descendantSamples(subjectChild.child.pid)); } catch (_) {}
  }, 5000);
  let uploader = null, stopping = false, signal = null;
  const pending = new Set();
  const timers = [];
  const stop = value => {
    stopping = true; signal = value; helperAbort.abort();
    // Forward cancellation to the subject; no extra execution grace.
    try { process.kill(-subjectChild.child.pid, value); } catch (_) {}
    for (const timer of timers) clearTimeout(timer);
  };
  process.once('SIGINT', () => stop('SIGINT'));
  process.once('SIGTERM', () => stop('SIGTERM'));
  async function checkpoint(sequence) {
    if (stopping) return;
    if (uploader) { transports[sequence].transport = 'overlap'; return; }
    const slot = path.join(directory, 'checkpoints', `slot-${sequence}`);
    try {
      writeAtomic(transportFile, transports);
      writeAtomic(path.join(directory, "stack-request.json"), {sequence});
      if (!await python(executable, [observer, 'checkpoint', '--directory', directory,
          '--sequence', String(sequence), '--elapsed-ms', String(Math.floor(performance.now() - started)),
          '--transport', transportFile], environment, subject, helperAbort.signal)) {
        transports[sequence].transport = 'unavailable'; return;
      }
      if (!await python(executable, [observer, 'check-seal', '--directory', slot,
          '--binding', path.join(inventory, 'binding.json')], environment, subject, helperAbort.signal)) {
        transports[sequence].transport = 'unavailable'; return;
      }
      const name = `ci-watchdog-unit-1-${binding.run}-${binding.attempt}-${binding.nonce.slice(0, 12)}-${sequence}`;
      uploader = boundedUpload({name, directory: slot}, process.env, 60000, __filename, helperAbort.signal);
      const publication = await uploader;
      const result = publication.result;
      transports[sequence] = {sequence, scheduled_s: SCHEDULE[sequence], transport: result.code,
        artifact_id: result.id, digest: result.digest, size: result.size};
    } catch (_) { transports[sequence].transport = 'unavailable'; }
    finally { uploader = null; }
  }
  for (let sequence = 0; sequence < SCHEDULE.length; sequence++) {
    timers.push(setTimeout(() => {
      const task = checkpoint(sequence);
      pending.add(task); task.finally(() => pending.delete(task));
    }, SCHEDULE[sequence] * 1000));
  }
  const outcome = await subjectChild.done;
  stopping = true; helperAbort.abort();
  for (const timer of timers) clearTimeout(timer);
  clearInterval(sampleTimer);
  const completed = await cleanup(subjectChild);
  // Never wait sixty seconds after subject exit for optional diagnostics.
  await Promise.race([Promise.allSettled([...pending]), delay(2500)]);
  for (const slot of transports) if (slot.transport === 'pending') slot.transport = signal ? 'cancelled' : 'unavailable';
  writeAtomic(path.join(directory, 'transport-final.json'), {complete: false, slots: transports,
    subject_status: outcome.code, subject_signal: outcome.signal, owned_subject_cleanup: completed,
    owned_uploader_cleanup: !uploader, diagnostic_only: true});
  if (signal || outcome.code === null) return 2;
  return outcome.code === 0 && !completed ? 2 : outcome.code;
}
async function uploaderMain() {
  process.once('message', async message => {
    let output;
    try {
      // Reuse the sole strict diagnostic reader immediately before SDK admission.
      // This helper receives no SDK credential environment and never runs pytest.
      const workspace = process.env.GITHUB_WORKSPACE;
      const inventory = path.join(process.env.RUNNER_TEMP, 'watchdog-current-inventory', 'binding.json');
      const reader = path.join(workspace, 'capture', 'scripts', 'ci_watchdog_diagnostics.py');
      const interpreter = path.join(workspace, 'subject', '.venv', 'bin', 'python');
      if (!await python(interpreter, [reader, 'check-seal', '--directory', message.directory,
          '--binding', inventory], subjectEnvironment(process.env), workspace)) {
        if (process.send) process.send(unknown(), () => process.exit(0));
        else process.exit(2);
        return;
      }
      const {DefaultArtifactClient} = require('@actions/artifact');
      output = await uploadWithClient(new DefaultArtifactClient(), message);
    } catch (_) { output = unknown(); }
    if (process.send) process.send(output, () => process.exit(0));
    else process.exit(2);
  });
}
module.exports = {subjectEnvironment, uploaderEnvironment, finalized, sealedFiles, uploadWithClient,
  numericProcess, descendantSamples, managed, cleanup, boundedUpload, runAction, writeAtomic};
if (require.main === module) {
  if (process.env.WATCHDOG_UPLOADER === '1') uploaderMain();
  else runAction().then(code => { process.exitCode = code; }).catch(() => {
    process.stderr.write('watchdog diagnostic unavailable: UNKNOWN\n'); process.exitCode = 2;
  });
}
