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
      const malformedModes = new Set(['malformed', 'wire-missing', 'wire-extra', 'wire-boolean',
        'wire-site', 'wire-kind', 'wire-operation', 'wire-mask', 'wire-accepted-enoent15',
        'wire-accepted-enoent31', 'wire-null', 'wire-zero-count']);
      for (const mode of ['permission', 'task-overflow', 'deadline', 'healthy', ...malformedModes]) {
        const root = path.join(directory, `${entry.slice(0,3)}-${mode}`);
        const bin = path.join(root, 'bin'), wrapper = path.join(root, 'helper.py');
        const marker = path.join(root, 'started');
        fs.mkdirSync(bin, {recursive:true});
        const helper = path.resolve(__dirname, '..', entry.startsWith('src/') ? 'src/supervisor.py' : 'dist/supervisor.py');
        const python = path.resolve(__dirname, '../../../..', '.venv/bin/python3');
        fs.writeFileSync(wrapper, `import importlib.util,sys,errno
sys.dont_write_bytecode=True
spec=importlib.util.spec_from_file_location('owned_helper',${JSON.stringify(helper)})
s=importlib.util.module_from_spec(spec);spec.loader.exec_module(s)
mode=${JSON.stringify(mode)}
if mode=='permission' or mode.startswith('wire-'):
 original_open=open
 later_refusal=[False]
 def denied(file,*args,**kwargs):
  if str(file).endswith('/children'):
   raise PermissionError(errno.EPERM if later_refusal[0] else errno.EACCES,'PRIVATE_ERROR_CANARY')
  return original_open(file,*args,**kwargs)
 s.open=denied
 if mode=='permission':
  original_children=s.children
  latch_checked=[False]
  def retained_first(pid,diagnostics=None,site=0):
   result=original_children(pid,diagnostics,site)
   if diagnostics is not None and diagnostics['counts']['scan_refused'] and not latch_checked[0]:
    # Two actual refused scans of the same owned actor, with different errno
    # categories, prove emitted first-record retention independently of counts.
    first=dict(diagnostics['first_scan_refusal'])
    assert first=={'s':1,'k':3,'o':2,'b':7}
    before=diagnostics['counts']['scan_refused']
    later_refusal[0]=True
    second=original_children(pid,diagnostics,site=3)
    assert second[1] is True
    assert diagnostics['counts']['scan_refused']==before+1
    assert diagnostics['first_scan_refusal']==first
    latch_checked[0]=True
   return result
  s.children=retained_first
if mode=='task-overflow':s.MAX_PROCESSES=1
if mode=='deadline':
 original_wait=s.os.waitpid
 def no_echild(pid,flags):
  try:return original_wait(pid,flags)
  except ChildProcessError:return (0,0)
 s.os.waitpid=no_echild
if mode=='malformed' or mode.startswith('wire-'):
 original_emit=s.emit
 def malformed(value):
  if value.get('event')=='diagnostic':
   value={**value,'counts':{**value['counts']}}
   if mode=='malformed':value['raw']='PRIVATE_CONTROL_CANARY'
   elif mode=='wire-missing':value.pop('first_scan_refusal')
   else:
    first=dict(value['first_scan_refusal'])
    if mode=='wire-extra':first['raw']='PRIVATE_CONTROL_CANARY'
    elif mode=='wire-boolean':first['s']=True
    elif mode=='wire-site':first['s']=0
    elif mode=='wire-kind':first['k']=7
    elif mode=='wire-operation':first['o']=0
    elif mode=='wire-mask':first['b']=4
    elif mode=='wire-accepted-enoent15':first.update(k=1,b=15)
    elif mode=='wire-accepted-enoent31':first.update(k=1,b=31)
    elif mode=='wire-null':first=None
    elif mode=='wire-zero-count':value['counts']['scan_refused']=0
    value['first_scan_refusal']=first
  return original_emit(value)
 s.emit=malformed
raise SystemExit(s.main())
`);
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
          if (malformedModes.has(mode)) {
            assert.equal(diagnostic.control_valid, false);
            assert.equal(diagnostic.supervisor, null);
          } else {
            const observed = diagnostic.supervisor;
            assert.ok(observed);
            assert.equal(observed.reason, mode === 'healthy' ? 'settled' : mode === 'deadline' ? 'settlement_deadline' : 'latched_refusal');
            for (const [key, value] of Object.entries(observed.counts)) {
              assert.ok(Number.isSafeInteger(value) && value >= 0 && value <= 65535, key);
            }
            if (mode === 'permission') {
              assert.ok(observed.counts.scan_refused > 0);
              assert.ok(observed.counts.scan_refused >= 2, 'both positioned refusals were counted');
              assert.deepEqual(observed.first_scan_refusal, {s:1, k:3, o:2, b:7});
              assert.equal(diagnostic.control_valid, true);
              assert.equal(diagnostic.settled, false);
            }
            if (mode === 'healthy') assert.equal(observed.first_scan_refusal, null);
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
