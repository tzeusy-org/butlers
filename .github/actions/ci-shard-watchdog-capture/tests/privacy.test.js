'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {boundedUpload} = require('../src/index');
const crypto = require('node:crypto');

test('raw SDK output errors and provider payload never enter retained IPC', async () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'watchdog-privacy-'));
  try {
    const fixture = path.join(directory, 'uploader.js');
    const snapshot = Buffer.from(JSON.stringify({schema:'ci-watchdog-diagnostic.v1',complete:false}));
    fs.writeFileSync(path.join(directory,'snapshot.json'),snapshot);
    fs.writeFileSync(path.join(directory,'manifest.json'),JSON.stringify({schema:'ci-watchdog-manifest.v1',complete:false,
      files:{'snapshot.json':{bytes:snapshot.length,sha256:crypto.createHash('sha256').update(snapshot).digest('hex')}}}));
    const actualSource=path.resolve(__dirname,'../src/index.js');
    fs.writeFileSync(fixture, `const {uploadWithClient}=require(${JSON.stringify(actualSource)});
      process.once('message', async message => {
        const client={async uploadArtifact() {
          console.log('PRIVATE_STDOUT_CANARY');console.error('PRIVATE_ERROR_CANARY');
          throw new Error('PRIVATE_SDK_PROVIDER_PAYLOAD_CANARY');
        }};
        if (process.env.SUBJECT_CANARY) process.send({raw:'SUBJECT_ENV_LEAK'});
        const result=await uploadWithClient(client,message);
        process.send(result,()=>process.exit(0));
      });`);
    const output = await boundedUpload({name:`ci-watchdog-unit-1-123-1-${'a'.repeat(12)}-0`,directory}, {PATH:process.env.PATH,
      SUBJECT_CANARY:'PRIVATE_ENV_CANARY'}, 1000, fixture);
    assert.equal(output.cleanup_complete, true);
    assert.deepEqual(output.result, {code:'failed', id:null, digest:null, size:null});
    assert.ok(!JSON.stringify(output).includes('PRIVATE'));
    fs.writeFileSync(fixture, `process.once('message', () => {
      process.send({code:'finalized',id:9,digest:'${'a'.repeat(64)}',size:0,raw:'PRIVATE_PROVIDER_CANARY'},()=>process.exit(0));
    });`);
    assert.equal((await boundedUpload({name:'ignored',directory}, {PATH:process.env.PATH}, 1000, fixture)).result.code,'failed');
    // Positioned positive establishes that the channel can carry a real finalized response.
    fs.writeFileSync(fixture, `process.once('message', () => {
      process.send({code:'finalized',id:9,digest:'${'a'.repeat(64)}',size:0},()=>process.exit(0));
    });`);
    const healthy = await boundedUpload({name:'ignored',directory}, {PATH:process.env.PATH}, 1000, fixture);
    assert.equal(healthy.result.code,'finalized'); assert.equal(healthy.cleanup_complete,true);
  } finally { fs.rmSync(directory, {recursive:true,force:true}); }
});
