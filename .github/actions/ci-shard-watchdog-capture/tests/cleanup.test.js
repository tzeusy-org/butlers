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
        assert.ok(numericProcess(sentinel.pid));
      } finally {if(pid){try{process.kill(-pid,'SIGKILL')}catch(_){}}action.kill('SIGKILL');}
    }
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
    const timeout = await boundedUpload({name:'ignored',directory},{PATH:process.env.PATH},150,uploader);
    assert.equal(timeout.result.code,'failed'); assert.equal(timeout.cleanup_complete,true);
    assert.ok(numericProcess(sentinel.pid));
    const abort=new AbortController();
    const cancelled=boundedUpload({name:'ignored',directory},{PATH:process.env.PATH},10000,uploader,abort.signal);
    setTimeout(()=>abort.abort(),100);
    const cancelStart=performance.now(); const cancellation=await cancelled;
    assert.ok(performance.now()-cancelStart<2500);
    assert.equal(cancellation.result.code,'failed');assert.equal(cancellation.cleanup_complete,true);
  } finally {sentinel.kill('SIGKILL');fs.rmSync(directory,{recursive:true,force:true});}
});
