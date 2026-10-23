'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {spawn} = require('node:child_process');
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

// REQ-testing-060: distinguish observed refusal species without granting completion.
test('closed cleanup diagnostics distinguish refused scans, overflow and settlement deadline', async () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'watchdog-refusal-'));
  const sentinel = spawn(process.execPath, ['-e', 'setInterval(()=>{},1000)'], {stdio:'ignore'});
  const sentinelExited = new Promise(resolve => sentinel.once('exit', resolve));
  const receipts = [];
  try {
    for (const entry of ['src/index.js', 'dist/index.js']) {
      const old = process.env.WATCHDOG_UPLOADER;
      process.env.WATCHDOG_UPLOADER = '1';
      let api;
      try { api = require(path.resolve(__dirname, '..', entry)); }
      finally { if (old === undefined) delete process.env.WATCHDOG_UPLOADER; else process.env.WATCHDOG_UPLOADER = old; }
      for (const mode of ['permission', 'task-overflow', 'deadline', 'healthy', 'malformed']) {
        const root = path.join(directory, `${entry.slice(0,3)}-${mode}`);
        const bin = path.join(root, 'bin'), wrapper = path.join(root, 'helper.py');
        const marker = path.join(root, 'started');
        fs.mkdirSync(bin, {recursive:true});
        const helper = path.resolve(__dirname, '..', entry.startsWith('src/') ? 'src/supervisor.py' : 'dist/supervisor.py');
        const python = path.resolve(__dirname, '../../../..', '.venv/bin/python3');
        fs.writeFileSync(wrapper, `import importlib.util,sys,errno\nsys.dont_write_bytecode=True\nspec=importlib.util.spec_from_file_location('owned_helper',${JSON.stringify(helper)})\ns=importlib.util.module_from_spec(spec);spec.loader.exec_module(s)\nmode=${JSON.stringify(mode)}\nif mode=='permission':\n original_open=open\n def denied(file,*args,**kwargs):\n  if str(file).endswith('/children'):raise PermissionError(errno.EACCES,'PRIVATE_ERROR_CANARY')\n  return original_open(file,*args,**kwargs)\n s.open=denied\nif mode=='task-overflow':s.MAX_PROCESSES=1\nif mode=='deadline':\n original_wait=s.os.waitpid\n def no_echild(pid,flags):\n  try:return original_wait(pid,flags)\n  except ChildProcessError:return (0,0)\n s.os.waitpid=no_echild\nif mode=='malformed':\n original_emit=s.emit\n def malformed(value):\n  if value.get('event')=='diagnostic':value={**value,'raw':'PRIVATE_CONTROL_CANARY'}\n  return original_emit(value)\n s.emit=malformed\nraise SystemExit(s.main())\n`);
        fs.writeFileSync(path.join(bin, 'python3'), `#!${python}\nimport os,sys\nos.execv(${JSON.stringify(python)},[${JSON.stringify(python)},${JSON.stringify(wrapper)},*sys.argv[2:]])\n`, {mode:0o755});
        const handle = api.managed(process.execPath, ['-e', `process.on('SIGTERM',()=>{});require('node:fs').writeFileSync(${JSON.stringify(marker)},String(process.pid));console.log('PRIVATE_CHILD_OUTPUT_CANARY');setInterval(()=>{},1000)`], {env:{PATH:bin+path.delimiter+process.env.PATH}});
        let target;
        try {
          assert.equal(await handle.launched, true);
          for (let tries=0; tries<200 && !fs.existsSync(marker); tries++) await delay(10);
          assert.ok(fs.existsSync(marker));
          target = api.numericProcess(Number(fs.readFileSync(marker, 'utf8')));
          assert.ok(target && !['Z','X'].includes(target.state));
          if (mode === 'deadline') {
            // Position missing kernel ECHILD proof, not a longer production budget.
            handle.child.kill('SIGTERM');
            const began = performance.now();
            while (!handle.isExited() && performance.now()-began<2200) await delay(10);
            assert.equal(handle.isExited(), true);
          }
          const complete = await api.cleanup(handle);
          assert.equal(complete, mode === 'healthy');
          const diagnostic = handle.diagnostic?.();
          assert.ok(diagnostic, 'closed refusal diagnostic must survive incomplete settlement');
          assert.equal(diagnostic.helper_exited, true);
          assert.equal(diagnostic.live_owned_count, 0);
          assert.equal(JSON.stringify(diagnostic).includes('PRIVATE'), false);
          assert.ok(JSON.stringify(diagnostic).length < 2048);
          if (mode === 'malformed') {
            assert.equal(diagnostic.control_valid, false);
            assert.equal(diagnostic.supervisor, null);
          } else {
            const observed = diagnostic.supervisor;
            assert.ok(observed);
            assert.equal(observed.reason, mode === 'healthy' ? 'settled' : mode === 'deadline' ? 'settlement_deadline' : 'latched_refusal');
            for (const [key, value] of Object.entries(observed.counts)) {
              assert.ok(Number.isSafeInteger(value) && value >= 0 && value <= 65535, key);
            }
            if (mode === 'permission') assert.ok(observed.counts.scan_refused > 0);
            if (mode === 'task-overflow') assert.ok(observed.counts.task_overflow > 0);
            if (mode === 'deadline') assert.equal(observed.kernel_children_closed, false);
          }
          const now = api.numericProcess(target.pid);
          assert.ok(!now || now.start_ticks !== target.start_ticks || ['Z','X'].includes(now.state));
          assert.ok(api.numericProcess(sentinel.pid));
          receipts.push({entry, mode, complete, diagnostic, sentinel_survived:true});
        } finally {
          await api.cleanup(handle, 600);
          if (target) {
            const now = api.numericProcess(target.pid);
            if (now && now.start_ticks === target.start_ticks && !['Z','X'].includes(now.state)) {
              try { process.kill(target.pid, 'SIGKILL'); } catch (_) {}
            }
          }
          handle.child.kill('SIGKILL');
        }
      }
    }
    console.log(JSON.stringify({closed_refusal_controls:receipts}));
  } finally {
    sentinel.kill('SIGKILL'); await sentinelExited;
    fs.rmSync(directory, {recursive:true, force:true});
    console.log(JSON.stringify({sentinel_actual_exit:true, signal:sentinel.signalCode}));
  }
});
