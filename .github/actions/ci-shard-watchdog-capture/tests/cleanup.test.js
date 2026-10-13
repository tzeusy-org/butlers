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
