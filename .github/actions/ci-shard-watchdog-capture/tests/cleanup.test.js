'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {spawn} = require('node:child_process');
const {managed, cleanup, numericProcess, boundedUpload} = require('../src/index');
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

test('healthy cancellation and resistant descendants attest bounded owned completion', async () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'watchdog-cleanup-'));
  const sentinel = spawn(process.execPath, ['-e','setInterval(()=>{},1000)'], {stdio:'ignore'});
  const sentinelExited=new Promise(resolve=>sentinel.once('exit',resolve));
  try {
    const healthy = managed(process.execPath, ['-e','process.exit(0)'], {env:{PATH:process.env.PATH}});
    assert.equal((await healthy.done).code,0); assert.equal(await cleanup(healthy),true);
    const childFile = path.join(directory,'child.pid');
    const parent = path.join(directory,'parent.js');
    fs.writeFileSync(parent, `const fs=require('node:fs'),{spawn}=require('node:child_process');
      const child=spawn(process.execPath,['-e',"process.on('SIGTERM',()=>{});setInterval(()=>{},1000)"],{stdio:'ignore'});
      fs.writeFileSync(${JSON.stringify(childFile)},String(child.pid));
      process.on('SIGTERM',()=>{});setInterval(()=>{},1000);`);
    const resistant = managed(process.execPath, [parent], {env:{PATH:process.env.PATH}});
    for (let tries=0;tries<100&&!fs.existsSync(childFile);tries++) await delay(10);
    assert.ok(fs.existsSync(childFile));
    const childPid=Number(fs.readFileSync(childFile,'utf8')); await delay(80);
    assert.ok(numericProcess(childPid));
    const started=performance.now(); assert.equal(await cleanup(resistant,600),true);
    assert.ok(performance.now()-started<1200);
    assert.ok(!numericProcess(childPid)||['Z','X'].includes(numericProcess(childPid).state));
    assert.ok(numericProcess(sentinel.pid));
    // A successful parent with a live resistant descendant still owns cleanup.
    fs.writeFileSync(parent, `const fs=require('node:fs'),{spawn}=require('node:child_process');
      const child=spawn(process.execPath,['-e',"process.on('SIGTERM',()=>{});setInterval(()=>{},1000)"],{stdio:'ignore'});
      fs.writeFileSync(${JSON.stringify(childFile)},String(child.pid));setTimeout(()=>process.exit(0),150);`);
    const orphan=managed(process.execPath,[parent],{env:{PATH:process.env.PATH}});
    assert.equal((await orphan.done).code,0);
    const orphanPid=Number(fs.readFileSync(childFile,'utf8'));
    assert.ok(numericProcess(orphanPid));
    assert.equal(await cleanup(orphan,600),true);
    assert.ok(!numericProcess(orphanPid)||['Z','X'].includes(numericProcess(orphanPid).state));
    assert.ok(numericProcess(sentinel.pid));
    // Immediate direct-parent departure cannot outrun ownership registration.
    const fastChild = path.join(directory,'fast-child.js');
    fs.writeFileSync(fastChild,"process.on('SIGTERM',()=>{});setInterval(()=>{},1000);");
    for (let index=0;index<3;index++) {
      const pidFile=path.join(directory,`fast-${index}.pid`);
      const fast=managed('/bin/sh',['-c',`"${process.execPath}" "${fastChild}" >/dev/null 2>&1 & echo $! > "${pidFile}"; exit 0`],{env:{PATH:process.env.PATH}});
      assert.equal((await fast.done).code,0);
      const pid=Number(fs.readFileSync(pidFile,'utf8'));
      assert.equal(await cleanup(fast,600),true);
      assert.ok(!numericProcess(pid)||['Z','X'].includes(numericProcess(pid).state));
      assert.ok(numericProcess(sentinel.pid));
    }
    // A ready resistant child can create its own session before becoming an
    // orphan; the kernel subreaper still owns it and must attest settlement.
    const ready=path.join(directory,'detached-ready'),term=path.join(directory,'detached-term');
    fs.writeFileSync(fastChild,`const fs=require('node:fs');process.on('SIGTERM',()=>fs.writeFileSync(${JSON.stringify(term)},'received'));fs.writeFileSync(${JSON.stringify(ready)},'ready');setInterval(()=>{},1000);`);
    fs.writeFileSync(parent,`const fs=require('node:fs'),{spawn}=require('node:child_process');const child=spawn(process.execPath,[${JSON.stringify(fastChild)}],{detached:true,stdio:'ignore'});child.unref();fs.writeFileSync(${JSON.stringify(childFile)},String(child.pid));const timer=setInterval(()=>{if(fs.existsSync(${JSON.stringify(ready)})){clearInterval(timer);process.exit(0)}},1);`);
    const detached=managed(process.execPath,[parent],{env:{PATH:process.env.PATH}});
    assert.equal((await detached.done).code,0);
    const detachedPid=Number(fs.readFileSync(childFile,'utf8'));
    assert.equal(await cleanup(detached,600),true);
    assert.ok(fs.existsSync(term));
    assert.ok(!numericProcess(detachedPid)||['Z','X'].includes(numericProcess(detachedPid).state));
    assert.ok(numericProcess(sentinel.pid));
    const restored=managed(process.execPath,['-e','process.exit(0)'],{env:{PATH:process.env.PATH}});
    assert.equal((await restored.done).code,0);assert.equal(await cleanup(restored),true);
    // Exercise actual runAction signal handling, not only cleanup in isolation.
    for (const entry of ['src/index.js','dist/index.js']) for (const mode of ['healthy','resistant','restored']) {
      const root=path.join(directory,`action-${entry.slice(0,3)}-${mode}`),subject=path.join(root,'subject'),capture=path.join(root,'capture'),inventory=path.join(root,'inventory'),output=path.join(root,'output'),bin=path.join(root,'bin');
      for(const target of [path.join(subject,'.venv/bin'),capture,inventory,output,bin])fs.mkdirSync(target,{recursive:true});
      fs.writeFileSync(path.join(subject,'.venv/bin/python'),'#!/bin/sh\nexit 0\n',{mode:0o755});
      const subjectPid=path.join(root,'subject.pid');
      fs.writeFileSync(path.join(bin,'uv'),`#!${process.execPath}\nconst fs=require('node:fs');fs.writeFileSync(${JSON.stringify(subjectPid)},String(process.pid));${mode==='resistant'?"process.on('SIGTERM',()=>{});":''}setInterval(()=>{},1000);\n`,{mode:0o755});
      fs.writeFileSync(path.join(inventory,'binding.json'),JSON.stringify({profile:'original-pr4484-unit1',run:'123',attempt:'1',repository:'tzeusy-org/butlers',workflow:'CI Shard Watchdog Diagnostic',provider_head:'b'.repeat(40),subject_head:'c'.repeat(40),nonce:'a'.repeat(64)}));
      const action=spawn(process.execPath,[path.resolve(__dirname,'..',entry)],{env:{PATH:bin+path.delimiter+process.env.PATH,INPUT_CAPTURE:capture,INPUT_SUBJECT:subject,INPUT_INVENTORY:inventory,INPUT_OUTPUT:output,GITHUB_RUN_ID:'123',GITHUB_RUN_ATTEMPT:'1',GITHUB_SHA:'b'.repeat(40)},stdio:'ignore'});
      let actionExited=false;action.once('exit',()=>actionExited=true);
      let pid;
      try {
        for(let tries=0;tries<200&&!fs.existsSync(subjectPid);tries++)await delay(10);
        assert.ok(fs.existsSync(subjectPid),'actual original UV entry reached');
        pid=Number(fs.readFileSync(subjectPid,'utf8'));await delay(100);
        const began=performance.now();action.kill('SIGTERM');
        while(!actionExited&&performance.now()-began<3500)await delay(10);
        assert.ok(actionExited,'actual cancellation must reach bounded settlement');
        assert.ok(!numericProcess(pid)||['Z','X'].includes(numericProcess(pid).state));
        const final=JSON.parse(fs.readFileSync(path.join(output,'transport-final.json')));
        assert.equal(final.complete,false);assert.equal(final.owned_subject_cleanup,true);
        assert.equal(final.owned_uploader_cleanup,true);assert.equal(final.subject_signal,'SIGTERM');
        assert.equal(final.cleanup_diagnostics.subject.supervisor.reason,'settled');
        assert.equal(final.cleanup_diagnostics.subject.helper_exited,true);
        assert.equal(final.cleanup_diagnostics.uploaders.length,8);
        assert.ok(JSON.stringify(final.cleanup_diagnostics).length<16384);
        assert.ok(numericProcess(sentinel.pid));
      } finally {if(pid){try{process.kill(-pid,'SIGKILL')}catch(_){}}action.kill('SIGKILL');}
    }
    // Position real task departure between listing and opening: an owned Node
    // child changes from live to same-start zombie without being reaped. Its
    // vanished task files are ENOENT, while permission/unknown/overflow refuse.
    const departedReceipts=[];
    for(const helperEntry of ['src/supervisor.py','dist/supervisor.py']) {
      const script=String.raw`import importlib.util,subprocess,os,signal,time,json,errno,sys,tempfile,contextlib
sys.dont_write_bytecode=True
spec=importlib.util.spec_from_file_location('supervisor',${JSON.stringify(path.resolve(__dirname,'..'))}+'/'+${JSON.stringify(helperEntry)})
s=importlib.util.module_from_spec(spec);spec.loader.exec_module(s)
rows=[]
for mode in ['healthy','neutralized','restored','permission','overflow']:
 child=subprocess.Popen([${JSON.stringify(process.execPath)},'-e','console.log("ready");setInterval(()=>{},1000)'],stdout=subprocess.PIPE,text=True)
 child.stdout.readline();before=s.process(child.pid);original=s.os.listdir;original_open=open;original_limit=s.MAX_PROCESSES
 try:
  def positioned(directory):
   tasks=original(directory)
   if directory==f'/proc/{child.pid}/task' and mode in ['healthy','neutralized','restored']:
    child.send_signal(signal.SIGKILL)
    for _ in range(1000):
     current=s.process(child.pid)
     if current and current[1]=='Z':break
     time.sleep(.001)
    assert current and current[0]==before[0] and current[1]=='Z'
   return tasks
  s.os.listdir=positioned
  if mode=='neutralized':
   # Execute the original departing-task error policy as an unsafe companion.
   source=open(${JSON.stringify(path.resolve(__dirname,'../src/supervisor.py'))}).read()
   start=source.index('    except (OSError, ValueError) as error:')
   end=source.index('\n\ndef main():',start)
   legacy=source[:start]+'    except (OSError, ValueError):\n        return set(), process(pid) is not None\n'+source[end:]
   namespace={'__name__':'neutralized'};exec(compile(legacy,'neutralized-supervisor','exec'),namespace)
   scan=namespace['children']
  else:scan=s.children
  if mode=='permission':
   def denied(file,*args,**kwargs):
    if str(file).endswith('/children'):raise PermissionError(errno.EACCES,'denied')
    return original_open(file,*args,**kwargs)
   s.open=denied
  if mode=='overflow':s.MAX_PROCESSES=1
  descendants,truncated=scan(child.pid)
  current=s.process(child.pid)
  if mode=='permission':del s.open
  row={'mode':mode,'pid':child.pid,'before':before,'after':current,'truncated':truncated,'descendant_count':len(descendants)}
  assert truncated==(mode in ['neutralized','permission','overflow'])
 finally:
  s.os.listdir=original;s.MAX_PROCESSES=original_limit
  if hasattr(s,'open'):del s.open
  os.kill(child.pid,signal.SIGKILL);reaped,status=os.waitpid(child.pid,0);child.returncode=os.waitstatus_to_exitcode(status)
  row['actual_kernel_reaped']=reaped==child.pid;row['gone_after_reaping']=s.process(child.pid) is None
  assert row['actual_kernel_reaped'] and row['gone_after_reaping']
  child.stdout.close()
 rows.append(row)
# Preserve the original five modes above; add the combined overflow/departure
# boundary with actual seven-task children, independent of unspecified ordering.
for mode in ['composite-overflow','composite-neutralized','composite-restored','file-overflow','file-neutralized','file-restored']:
 child=subprocess.Popen([${JSON.stringify(process.execPath)},'-e','console.log("ready");setInterval(()=>{},1000)'],stdout=subprocess.PIPE,text=True)
 child.stdout.readline();before=s.process(child.pid);original=s.os.listdir;original_limit=s.MAX_PROCESSES
 row={'mode':mode,'pid':child.pid,'before':before}
 listed_tasks=[]
 try:
  file_mode=mode.startswith('file-')
  s.MAX_PROCESSES=original_limit if file_mode else 1
  def depart():
   os.kill(child.pid,signal.SIGKILL)
   # A zombie leader alone does not prove every former task path vanished.
   # Position the actual ENOENT boundary used by this negative companion.
   former=[f'/proc/{child.pid}/task/{task}/children' for task in listed_tasks if task!=str(child.pid)]
   for _ in range(1000):
    current=s.process(child.pid)
    if current and current[1]=='Z' and all(not os.path.exists(p) for p in former):break
    time.sleep(.001)
   assert current and current[0]==before[0] and current[1]=='Z'
   assert former and all(not os.path.exists(p) for p in former)
   row['former_task_paths_gone']=True
   row['after']=current
  def combined(directory):
   tasks=original(directory)
   if directory==f'/proc/{child.pid}/task':
    tasks.sort(key=lambda task:task==str(child.pid))
    listed_tasks[:]=tasks
    row['task_count']=len(tasks);row['cap']=s.MAX_PROCESSES;row['known_overflow']=len(tasks)>s.MAX_PROCESSES;row['nonleader_first']=tasks[0]!=str(child.pid)
    assert row['nonleader_first'] and row['known_overflow']==(not file_mode)
    if not file_mode:depart()
   return tasks
  s.os.listdir=combined
  if file_mode:
   @contextlib.contextmanager
   def bounded_children():
    # Actual bounded read from a private regular-file seam; no invented proc
    # population. Departure follows this positive byte-overflow observation.
    with tempfile.TemporaryFile(mode='w+') as stream:
     stream.write(' '*65536);stream.seek(0);yield stream
    row['known_file_overflow_bytes']=65536;depart()
   first_children=[True]
   def bounded_open(file,*args,**kwargs):
    if str(file).endswith('/children') and first_children[0]:
     first_children[0]=False;return bounded_children()
    return original_open(file,*args,**kwargs)
   s.open=bounded_open
  if mode in ['composite-neutralized','file-neutralized']:
   source=open(${JSON.stringify(path.resolve(__dirname,'..'))}+'/'+${JSON.stringify(helperEntry)}).read()
   assert 'return set(), truncated' in source
   source=source.replace('return set(), truncated','return set(), False')
   namespace={'__name__':'composite_unsafe'};exec(compile(source,'composite-unsafe-supervisor','exec'),namespace);namespace['MAX_PROCESSES']=s.MAX_PROCESSES
   if file_mode:namespace['open']=bounded_open
   scan=namespace['children']
  else:scan=s.children
  descendants,truncated=scan(child.pid)
  row['truncated']=truncated;row['descendant_count']=len(descendants);row['overflow_invariant_pass']=truncated
  assert truncated==(mode not in ['composite-neutralized','file-neutralized'])
 finally:
  s.os.listdir=original;s.MAX_PROCESSES=original_limit
  if hasattr(s,'open'):del s.open
  os.kill(child.pid,signal.SIGKILL);reaped,status=os.waitpid(child.pid,0);child.returncode=os.waitstatus_to_exitcode(status);child.stdout.close()
  row['actual_kernel_reaped']=reaped==child.pid;row['gone_after_reaping']=s.process(child.pid) is None
  assert row['actual_kernel_reaped'] and row['gone_after_reaping']
 rows.append(row)
assert s.children(child.pid)[1] is True
print(json.dumps({'rows':rows,'unknown_reaped_actor_refused':True}))
`;
      const probe=spawn('python3',['-c',script],{env:{PATH:process.env.PATH},stdio:['ignore','pipe','ignore']});
      let output='';probe.stdout.on('data',chunk=>{output+=chunk;assert.ok(output.length<16384)});
      const result=await new Promise(resolve=>probe.once('close',(code,signal)=>resolve({code,signal})));
      assert.deepEqual(result,{code:0,signal:null});
      const receipt=JSON.parse(output);departedReceipts.push({helperEntry,...receipt});
      assert.ok(numericProcess(sentinel.pid));
    }
    console.log(JSON.stringify({departed_task_controls:departedReceipts}));
    // Control-reader loss alone must settle with the actual owner still alive.
    // Neutralize only the health check in an executing helper to prove this
    // assertion observes owned execution, rather than cleanup after the fact.
    const originalPython=path.join(path.resolve(__dirname,'../../../..','.venv'),'bin/python3');
    const mutant=path.join(directory,'pipe-check-disabled.py'),mutantBin=path.join(directory,'mutant-bin');
    const helperBody=fs.readFileSync(path.resolve(__dirname,'../src/supervisor.py'),'utf8');
    assert.ok(helperBody.includes('if control.poll(0):'));
    fs.writeFileSync(mutant,helperBody.replace('if control.poll(0):','if False:'));
    fs.mkdirSync(mutantBin);
    fs.writeFileSync(path.join(mutantBin,'python3'),`#!${originalPython}\nimport os,sys\nos.execv(${JSON.stringify(originalPython)},[${JSON.stringify(originalPython)},${JSON.stringify(mutant)},*sys.argv[2:]])\n`,{mode:0o755});
    const pipeReceipts=[];
    for(const entry of ['src/index.js','dist/index.js']) {
      // ncc's distributed entry executes its entry guard on require. Select
      // the idle uploader message listener while exercising its real exports;
      // no message or provider invocation occurs.
      const previousUploader=process.env.WATCHDOG_UPLOADER;
      process.env.WATCHDOG_UPLOADER='1';
      let api;
      try {api=require(path.resolve(__dirname,'..',entry));}
      finally {if(previousUploader===undefined)delete process.env.WATCHDOG_UPLOADER;else process.env.WATCHDOG_UPLOADER=previousUploader;}
      for(const mode of ['healthy','pipe-lost','neutralized','restored']) {
        const marker=path.join(directory,`pipe-${entry.slice(0,3)}-${mode}.pid`);
        const handle=api.managed(process.execPath,['-e',`const fs=require('node:fs');process.on('SIGTERM',()=>{});fs.writeFileSync(${JSON.stringify(marker)},String(process.pid));setInterval(()=>{},1000);`],{env:{PATH:(mode==='neutralized'?mutantBin+path.delimiter:'')+process.env.PATH}});
        let target;
        try {
          assert.equal(await handle.launched,true);
          for(let tries=0;tries<100&&!fs.existsSync(marker);tries++)await delay(10);
          assert.ok(fs.existsSync(marker));target=api.numericProcess(Number(fs.readFileSync(marker,'utf8')));
          assert.ok(target&&!['Z','X'].includes(target.state));
          const owner=api.numericProcess(process.pid),began=performance.now();
          if(mode==='pipe-lost'||mode==='neutralized')handle.child.stdout.destroy();else handle.child.kill('SIGTERM');
          // Observe before calling cleanup: recovery must not mask this boundary.
          await delay(1700);
          const live=pid=>{const current=api.numericProcess(pid);return !!(current&&!['Z','X'].includes(current.state))};
          const receipt={entry,mode,owner_pid:process.pid,owner_start:owner.start_ticks,owner_live:live(process.pid),target_pid:target.pid,target_start:target.start_ticks,supervisor_pid:handle.child.pid,elapsed_ms:performance.now()-began,supervisor_live:live(handle.child.pid),target_live:live(target.pid),sentinel_live:live(sentinel.pid)};
          pipeReceipts.push(receipt);
          assert.equal(receipt.owner_live,true);assert.equal(receipt.sentinel_live,true);
          assert.equal(receipt.supervisor_live,mode==='neutralized');assert.equal(receipt.target_live,mode==='neutralized');
          const complete=await api.cleanup(handle,600);
          assert.equal(complete,mode==='healthy'||mode==='restored','lost acknowledgment must remain incomplete');
          assert.equal(live(target.pid),false);assert.equal(live(handle.child.pid),false);
          assert.equal(live(sentinel.pid),true);
          receipt.recovery_cleanup_complete=complete;receipt.recovery_target_live=live(target.pid);receipt.recovery_supervisor_live=live(handle.child.pid);receipt.actual_exit_observed=handle.isExited();
          assert.equal(receipt.actual_exit_observed,true);
        } finally {
          await api.cleanup(handle,600);
          if(target){const current=api.numericProcess(target.pid);if(current&&current.start_ticks===target.start_ticks&&!['Z','X'].includes(current.state)){try{process.kill(target.pid,'SIGKILL')}catch(_){}}}
          handle.child.kill('SIGKILL');
        }
      }
    }
    console.log(JSON.stringify({pipe_loss_controls:pipeReceipts}));
    // Abrupt action-parent loss closes its pipe; owned cleanup still completes.
    const lostParent=path.join(directory,'lost-parent.js'),lostPid=path.join(directory,'lost-child.pid'),supervisorPid=path.join(directory,'lost-supervisor.pid');
    fs.writeFileSync(fastChild,`const fs=require('node:fs');process.on('SIGTERM',()=>{});fs.writeFileSync(${JSON.stringify(lostPid)},String(process.pid));setInterval(()=>{},1000);`);
    fs.writeFileSync(lostParent,`const fs=require('node:fs'),{managed}=require(${JSON.stringify(path.resolve(__dirname,'../src/index.js'))});const handle=managed(process.execPath,[${JSON.stringify(fastChild)}],{env:{PATH:process.env.PATH}});fs.writeFileSync(${JSON.stringify(supervisorPid)},String(handle.child.pid));`);
    const lost=spawn(process.execPath,[lostParent],{env:{PATH:process.env.PATH},stdio:'ignore'});
    let abandonedPid,abandonedSupervisor;
    try {
      for(let tries=0;tries<200&&!fs.existsSync(lostPid);tries++)await delay(10);
      assert.ok(fs.existsSync(lostPid));
      abandonedPid=Number(fs.readFileSync(lostPid,'utf8'));abandonedSupervisor=Number(fs.readFileSync(supervisorPid,'utf8'));
      lost.kill('SIGKILL');
      for(let tries=0;tries<100;tries++){const current=numericProcess(abandonedPid);if(!current||['Z','X'].includes(current.state))break;await delay(10)}
      assert.ok(!numericProcess(abandonedPid)||['Z','X'].includes(numericProcess(abandonedPid).state));
      for(let tries=0;tries<100;tries++){const current=numericProcess(abandonedSupervisor);if(!current||['Z','X'].includes(current.state))break;await delay(10)}
      assert.ok(!numericProcess(abandonedSupervisor)||['Z','X'].includes(numericProcess(abandonedSupervisor).state));
      assert.ok(numericProcess(sentinel.pid));
    } finally {for(const pid of [abandonedPid,abandonedSupervisor])if(pid){try{process.kill(pid,'SIGKILL')}catch(_){}}lost.kill('SIGKILL');}
    // An adopted/unrelated parent cannot stand in for the launching owner.
    const mustNotStart=path.join(directory,'wrong-owner-started');
    const ownerMismatch=spawn('python3',[path.resolve(__dirname,'../src/supervisor.py'),String(sentinel.pid),String(numericProcess(sentinel.pid).start_ticks),process.execPath,'-e',`require('node:fs').writeFileSync(${JSON.stringify(mustNotStart)},'started')`],{env:{PATH:process.env.PATH},stdio:'ignore'});
    const denied=await new Promise(resolve=>ownerMismatch.once('exit',code=>resolve(code)));
    assert.equal(denied,2);assert.equal(fs.existsSync(mustNotStart),false);
    assert.ok(numericProcess(sentinel.pid));
    // Neutralizing the supervisor cannot turn empty polling into completion.
    fs.writeFileSync(parent,`const fs=require('node:fs');fs.writeFileSync(${JSON.stringify(childFile)},String(process.pid));process.on('SIGTERM',()=>{});setInterval(()=>{},1000);`);
    fs.unlinkSync(childFile);
    const neutralized=managed(process.execPath,[parent],{env:{PATH:process.env.PATH}});
    for(let tries=0;tries<100&&!fs.existsSync(childFile);tries++)await delay(10);
    assert.ok(fs.existsSync(childFile));await delay(50);
    const unacknowledgedPid=Number(fs.readFileSync(childFile,'utf8'));
    neutralized.child.kill('SIGKILL');await neutralized.done;
    assert.equal(await cleanup(neutralized,600),false);
    assert.ok(!numericProcess(unacknowledgedPid)||['Z','X'].includes(numericProcess(unacknowledgedPid).state));
    assert.ok(numericProcess(sentinel.pid));
    const acknowledged=managed(process.execPath,['-e','process.exit(0)'],{env:{PATH:process.env.PATH}});
    assert.equal((await acknowledged.done).code,0);assert.equal(await cleanup(acknowledged),true);
    const uploader=path.join(directory,'uploader.js');
    fs.writeFileSync(uploader, `process.on('message',()=>{});process.on('SIGTERM',()=>{});setInterval(()=>{},1000);`);
    // Include interpreter startup and positive kernel settlement in this healthy envelope.
    const timeout = await boundedUpload({name:'ignored',directory},{PATH:process.env.PATH},1000,uploader);
    assert.equal(timeout.result.code,'failed'); assert.equal(timeout.cleanup_complete,true);
    assert.ok(numericProcess(sentinel.pid));
    const abort=new AbortController();
    const cancelled=boundedUpload({name:'ignored',directory},{PATH:process.env.PATH},10000,uploader,abort.signal);
    setTimeout(()=>abort.abort(),100);
    const cancelStart=performance.now(); const cancellation=await cancelled;
    assert.ok(performance.now()-cancelStart<2500);
    assert.equal(cancellation.result.code,'failed');assert.equal(cancellation.cleanup_complete,true);
  } finally {sentinel.kill('SIGKILL');fs.rmSync(directory,{recursive:true,force:true});await sentinelExited;console.log(JSON.stringify({sentinel_actual_exit:true,signal:sentinel.signalCode}));}
});
