'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const crypto = require('node:crypto');
const {uploadWithClient, finalized, subjectEnvironment, subjectInputs, uploaderEnvironment} = require('../src/index');

test('immutable finalized publication and closed IPC preserve credential separation', async () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'watchdog-publish-'));
  try {
    const snapshot = Buffer.from(JSON.stringify({schema: 'ci-watchdog-diagnostic.v1', complete: false}));
    fs.writeFileSync(path.join(directory, 'snapshot.json'), snapshot);
    fs.writeFileSync(path.join(directory, 'manifest.json'), JSON.stringify({schema: 'ci-watchdog-manifest.v1',
      complete: false, files: {'snapshot.json': {sha256: crypto.createHash('sha256').update(snapshot).digest('hex'), bytes: snapshot.length}}}));
    const names = new Set();
    const client = {async uploadArtifact(name, files, root, options) {
      assert.equal(root, directory);
      assert.deepEqual(files.map(x => path.basename(x)), ['manifest.json', 'snapshot.json']);
      assert.deepEqual(options, {retentionDays: 14, compressionLevel: 0});
      assert.ok(!names.has(name)); names.add(name);
      return {id: 42, digest: 'a'.repeat(64), size: snapshot.length};
    }};
    for (let index = 0; index < 8; index++) {
      const message = {name: `ci-watchdog-unit-1-123-1-${'a'.repeat(12)}-${index}`, directory};
      assert.equal((await uploadWithClient(client, message)).code, 'finalized');
    }
    assert.equal(names.size, 8);
    const subject = subjectInputs({GITHUB_SHA:'b'.repeat(40), ACTIONS_RUNTIME_TOKEN:'TOKEN_CANARY',
      PYTHONPATH:'/kept', CI_COVERAGE:'1', CI_UNIT_WORKERS:'9'}, {subject_head:'c'.repeat(40)}, '/capture');
    assert.equal(subject.GITHUB_SHA,'c'.repeat(40));
    assert.equal(subject.CI_WATCHDOG_PROVIDER_HEAD,'b'.repeat(40));
    assert.equal(subject.CI_COVERAGE,'0');assert.equal(subject.CI_UNIT_WORKERS,'3');
    assert.equal(subject.PYTHONPATH,['/capture/scripts','/kept'].join(path.delimiter));
    assert.equal(subject.ACTIONS_RUNTIME_TOKEN,undefined);
    assert.equal(finalized({code: 'finalized', id: 42, digest: 'a'.repeat(64), size: 1, raw: 'PRIVATE_CANARY'}), false);
    assert.equal(finalized({code: 'finalized', id: true, digest: 'a'.repeat(64), size: 1}), false);
    assert.equal(finalized({code: 'failed', id: 42, digest: 'a'.repeat(64), size: 1}), false);
    const environment = {ACTIONS_RUNTIME_TOKEN: 'TOKEN_CANARY', ACTIONS_RESULTS_URL: 'URL_CANARY',
      DB_SECRET: 'DB_CANARY', GITHUB_RUN_ID: '123', PATH: '/usr/bin'};
    assert.equal(subjectEnvironment(environment).ACTIONS_RUNTIME_TOKEN, undefined);
    assert.equal(subjectEnvironment(environment).DB_SECRET, 'DB_CANARY');
    assert.equal(uploaderEnvironment(environment).DB_SECRET, undefined);
    assert.equal(uploaderEnvironment(environment).ACTIONS_RUNTIME_TOKEN, 'TOKEN_CANARY');
    fs.unlinkSync(path.join(directory, 'snapshot.json'));
    fs.symlinkSync(path.join(directory, 'manifest.json'), path.join(directory, 'snapshot.json'));
    assert.equal((await uploadWithClient(client, {name: `ci-watchdog-unit-1-123-1-${'a'.repeat(12)}-0`, directory})).code, 'failed');
  } finally { fs.rmSync(directory, {recursive: true, force: true}); }
});
