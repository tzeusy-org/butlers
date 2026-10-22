'use strict';
// REQ-testing-060: software controls do not qualify hosted retention or the original workload.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {spawn} = require('node:child_process');
const api = require('../src/index');
const temporary = () => fs.mkdtempSync(path.join(os.tmpdir(), 'retention-test-'));
const binding = () => ({repository:'tzeusy-org/butlers', workflow:'CI Shard Watchdog Retention Control',
  run:'123', attempt:'1', source_head:'a'.repeat(40), source_tree:'b'.repeat(40), nonce:'c'.repeat(64),
  pins:Object.fromEntries(api.RETENTION_PINS.map(key => [key, 'd'.repeat(64)]))});
const environment = {GITHUB_REPOSITORY:'tzeusy-org/butlers', GITHUB_RUN_ID:'123', GITHUB_RUN_ATTEMPT:'1'};

test('one immutable control is finalized with separate schema, namespace and exact payload hashes', async () => {
  const directory = temporary();
  try {
    const message = api.writeRetentionControl(directory, binding());
    let calls = 0;
    const client = {async uploadArtifact(name, files, root, options) {
      calls++;
      assert.equal(root, directory); assert.equal(name, message.name);
      assert.deepEqual(files.map(file => path.basename(file)), ['manifest.json','marker.json']);
      assert.deepEqual(options, {retentionDays:14, compressionLevel:0});
      const manifest = JSON.parse(fs.readFileSync(files[0]));
      const marker = JSON.parse(fs.readFileSync(files[1]));
      assert.equal(marker.schema,'ci-watchdog-retention-control.v1');
      assert.equal(marker.complete,false); assert.equal(marker.mechanism_only,true);
      assert.ok(files.reduce((total,file) => total+fs.statSync(file).size,0)<=8192);
      assert.equal(manifest.files['marker.json'].sha256, require('node:crypto').createHash('sha256').update(fs.readFileSync(files[1])).digest('hex'));
      return {id:42,digest:'e'.repeat(64),size:1500};
    }};
    assert.equal((await api.uploadRetentionWithClient(client,message,environment)).code,'finalized');
    assert.equal(calls,1);
    assert.throws(()=>api.sealedFiles(directory));
    const interpreter=path.resolve(__dirname,'../../../../.venv/bin/python');
    const refusal=require('node:child_process').spawnSync(interpreter,['-c',
      "import json,sys;sys.path.insert(0,'scripts');import ci_partition as p,ci_watchdog_diagnostics as d;v=json.load(open(sys.argv[1]));n=0\nfor check in (lambda:p.require_receipt_types(v),lambda:d.read_checkpoint(__import__('pathlib').Path(sys.argv[2]),{})):\n try:check()\n except ValueError:n+=1\nassert n==2",path.join(directory,'marker.json'),directory],
      {cwd:path.resolve(__dirname,'../../../..'),timeout:10000,encoding:'utf8'});
    assert.equal(refusal.status,0,'real execution and diagnostic readers must both refuse control');

    assert.equal((await api.uploadWithClient(client,message)).code,'failed');
    assert.equal(calls,1,'diagnostic reader must refuse without calling SDK');
    assert.throws(()=>api.writeRetentionControl(directory,binding()),'immutable directory cannot be reused');
  } finally {fs.rmSync(directory,{recursive:true,force:true});}
});

test('mixed source, schema, namespace, extra data and altered payload refuse before SDK', async () => {
  const collapsed= binding();
  collapsed.pins={[[...api.RETENTION_PINS].sort().join(',')]:'d'.repeat(64)};
  const rejected=temporary();
  try {assert.throws(()=>api.writeRetentionControl(rejected,collapsed),'all ten individual source pins are required');}
  finally {fs.rmSync(rejected,{recursive:true,force:true});}
  for (const mode of ['name','run','source','extra','schema','payload','symlink','oversize','duplicate','hash']) {
    const directory=temporary();
    try {
      const message=api.writeRetentionControl(directory,binding());
      const markerFile=path.join(directory,'marker.json');
      fs.chmodSync(markerFile,0o600);
      if(mode==='hash') {const file=path.join(directory,'manifest.json');fs.chmodSync(file,0o600);const value=JSON.parse(fs.readFileSync(file));value.files['marker.json'].sha256='0'.repeat(64);fs.writeFileSync(file,JSON.stringify(value));}
      if(mode==='name') message.name='ci-unit-test-evidence';
      if(mode==='run') message.binding.run='124';
      if(mode==='source') message.binding.source_head='f'.repeat(40);
      if(mode==='extra') message.raw='PRIVATE_CANARY';
      if(mode==='schema') {const value=JSON.parse(fs.readFileSync(markerFile));value.schema='ci-watchdog-diagnostic.v1';fs.writeFileSync(markerFile,JSON.stringify(value));}
      if(mode==='payload') fs.appendFileSync(markerFile,' ');
      if(mode==='symlink') {fs.unlinkSync(markerFile);fs.symlinkSync('manifest.json',markerFile);}
      if(mode==='oversize') fs.writeFileSync(markerFile,'x'.repeat(8193));
      if(mode==='duplicate') fs.writeFileSync(markerFile,'{"schema":"wrong",'+fs.readFileSync(markerFile,'utf8').slice(1));
      let calls=0;
      const result=await api.uploadRetentionWithClient({async uploadArtifact(){calls++;throw new Error('PRIVATE_ERROR');}},message,environment);
      assert.equal(result.code,'failed',mode);assert.equal(calls,0,mode);
    } finally {fs.rmSync(directory,{recursive:true,force:true});}
  }
});

test('provider, workflow, checkout and admitted source must agree before any artifact exists', () => {
  const root=temporary();
  const {execFileSync}=require('node:child_process');
  const git=args=>execFileSync('git',args,{cwd:root,encoding:'utf8',stdio:['ignore','pipe','ignore']}).trim();
  try {
    git(['init','-q']);
    for(const file of api.RETENTION_PINS) {fs.mkdirSync(path.dirname(path.join(root,file)),{recursive:true});fs.writeFileSync(path.join(root,file),'opaque-source');}
    git(['add','.']);git(['-c','user.name=Control','-c','user.email=control@example.invalid','commit','-qm','control fixture']);
    const head=git(['rev-parse','HEAD']);
    const env={...environment,PATH:process.env.PATH,GITHUB_SHA:head,GITHUB_WORKFLOW_SHA:head,GITHUB_EVENT_NAME:'workflow_dispatch',GITHUB_WORKFLOW:'CI Shard Watchdog Retention Control',GITHUB_WORKFLOW_REF:'tzeusy-org/butlers/.github/workflows/ci-shard-watchdog-retention-control.yml@refs/heads/main'};
    const first=api.retentionBinding(root,head,env),second=api.retentionBinding(root,head,env);
    assert.equal(first.source_head,head);assert.equal(first.source_tree,git(['rev-parse','HEAD^{tree}']));
    assert.notEqual(first.nonce,second.nonce);
    for(const changes of [{GITHUB_SHA:'f'.repeat(40)},{GITHUB_WORKFLOW_SHA:'f'.repeat(40)},{GITHUB_EVENT_NAME:'push'},{GITHUB_WORKFLOW:'CI'},{GITHUB_RUN_ATTEMPT:'0'},{GITHUB_WORKFLOW_REF:'other/repo/workflow.yml@refs/heads/main'}])
      assert.throws(()=>api.retentionBinding(root,head,{...env,...changes}));
    fs.writeFileSync(path.join(root,api.RETENTION_PINS[0]),'changed');
    assert.throws(()=>api.retentionBinding(root,head,env));
  } finally {fs.rmSync(root,{recursive:true,force:true});}
});

test('owned uploader boundary rejects poisoned, missing, duplicate and cancelled results without harming sentinel', async () => {
  const directory=temporary();
  const sentinel=spawn(process.execPath,['-e','setInterval(()=>{},1000)'],{stdio:'ignore'});
  const sentinelExit=new Promise(resolve=>sentinel.once('exit',resolve));
  try {
    for(const mode of ['healthy','poison','duplicate','missing','cancel','resistant']) {
      const entry=path.join(directory,mode+'.js');
      const marker=path.join(directory,mode+'.started');
      fs.writeFileSync(entry,`process.once('message',()=>{require('node:fs').writeFileSync(${JSON.stringify(marker)},'ready');console.log('PRIVATE_STDOUT');console.error('PRIVATE_STDERR');${mode==='resistant'?`require('node:child_process').spawn(process.execPath,['-e',"process.on('SIGTERM',()=>{});setInterval(()=>{},1000)"],{detached:true,stdio:'ignore'});`:''}${['cancel','resistant'].includes(mode)?`process.on('SIGTERM',()=>{});setInterval(()=>{},1000);`: mode==='missing'?`process.exit(0);`:`process.send({code:'finalized',id:process.env.DB_SECRET?0:42,digest:'e'.repeat(64),size:100${mode==='poison'?",raw:'PRIVATE_IPC'":''}});${mode==='duplicate'?`process.send({code:'finalized',id:43,digest:'e'.repeat(64),size:100});`:''}setTimeout(()=>process.exit(0),20);`}});`);
      const abort=new AbortController();
      const pending=api.boundedUpload({}, {...process.env,DB_SECRET:'PRIVATE_ENV'},5000,entry,abort.signal);
      if(mode==='cancel'||mode==='resistant') {
        for(let tries=0;tries<200&&!fs.existsSync(marker);tries++) await new Promise(resolve=>setTimeout(resolve,10));
        assert.ok(fs.existsSync(marker),'actual uploader reached');abort.abort();
      }
      const result=await pending;
      assert.equal(result.result.code,mode==='healthy'?'finalized':'failed',mode);
      assert.equal(result.cleanup_complete,true,mode);
      assert.equal(JSON.stringify(result).includes('PRIVATE'),false);
      assert.ok(api.numericProcess(sentinel.pid));
    }
    assert.equal(api.uploaderEnvironment({DB_SECRET:'PRIVATE_ENV'}).DB_SECRET,undefined);
  } finally {sentinel.kill('SIGKILL');await sentinelExit;fs.rmSync(directory,{recursive:true,force:true});}
});

 test('SDK failure and malformed finalization never yield persistence proof or raw error', async()=>{
  const directory=temporary();
  try {
    const message=api.writeRetentionControl(directory,binding());
    for(const value of [null,{}, {id:0,digest:'e'.repeat(64),size:1},{id:42,digest:'bad',size:1},{id:42,digest:'e'.repeat(64),size:-1}]) {
      const result=await api.uploadRetentionWithClient({async uploadArtifact(){return value;}},message,environment);
      assert.equal(result.code,'failed');assert.equal(JSON.stringify(result).includes('PRIVATE'),false);
    }
    assert.equal((await api.uploadRetentionWithClient({async uploadArtifact(){throw new Error('PRIVATE_SDK_ERROR');}},message,environment)).code,'failed');
  } finally {fs.rmSync(directory,{recursive:true,force:true});}
});

test('actual control entry finalizes only after managed mock SDK settlement and exposes no SDK output', async()=>{
  const root=temporary();
  const {execFileSync}=require('node:child_process');
  const git=args=>execFileSync('git',args,{cwd:root,encoding:'utf8',stdio:['ignore','pipe','ignore']}).trim();
  try {
    const original=path.resolve(__dirname,'../../../..');
    const imported=require('node:child_process').spawnSync(process.execPath,['-e',
      `require(${JSON.stringify(path.join(original,'.github/actions/ci-shard-watchdog-capture/dist/index.js'))})`],
      {env:{PATH:process.env.PATH},timeout:10000,encoding:'utf8'});
    assert.equal(imported.status,0,'generated bundle import must never start the diagnostic');
    assert.equal(imported.stderr,'');assert.equal(imported.stdout,'');

    git(['init','-q']);
    for(const file of api.RETENTION_PINS) {
      fs.mkdirSync(path.dirname(path.join(root,file)),{recursive:true});
      fs.copyFileSync(path.join(original,file),path.join(root,file));
    }
    // The fixture's bundle is the real source entry, not an alternate implementation.
    fs.copyFileSync(path.join(original,'.github/actions/ci-shard-watchdog-capture/src/index.js'),path.join(root,'.github/actions/ci-shard-watchdog-capture/dist/index.js'));
    const sdk=path.join(root,'.github/actions/ci-shard-watchdog-capture/node_modules/@actions/artifact');
    fs.mkdirSync(sdk,{recursive:true});
    fs.writeFileSync(path.join(sdk,'index.js'),`exports.DefaultArtifactClient=class{async uploadArtifact(){console.log('PRIVATE_SDK_STDOUT');console.error('PRIVATE_SDK_STDERR');if(process.env.PRIVATE_SECRET)throw Error('PRIVATE_ENV');return {id:42,digest:'e'.repeat(64),size:1500};}};`);
    git(['add','.']);git(['-c','user.name=Control','-c','user.email=control@example.invalid','commit','-qm','controlled SDK fixture']);
    const head=git(['rev-parse','HEAD']);
    const action=spawn(process.execPath,[path.join(root,'.github/actions/ci-shard-watchdog-retention-control/index.js')],{env:{PATH:process.env.PATH,...environment,GITHUB_SHA:head,GITHUB_WORKFLOW_SHA:head,GITHUB_EVENT_NAME:'workflow_dispatch',GITHUB_WORKFLOW:'CI Shard Watchdog Retention Control',GITHUB_WORKFLOW_REF:'tzeusy-org/butlers/.github/workflows/ci-shard-watchdog-retention-control.yml@refs/heads/main',INPUT_CAPTURE_HEAD:head,PRIVATE_SECRET:'PRIVATE_CANARY'},stdio:['ignore','pipe','pipe']});
    let output='',error='';action.stdout.on('data',chunk=>output+=chunk);action.stderr.on('data',chunk=>error+=chunk);
    const timer=setTimeout(()=>action.kill('SIGTERM'),10000);
    const code=await new Promise(resolve=>action.once('close',resolve));clearTimeout(timer);
    assert.equal(code,0,error);assert.equal(error,'');assert.equal(output.includes('PRIVATE'),false);
    const record=JSON.parse(output);
    assert.equal(record.event,'finalized');assert.equal(record.owned_cleanup_complete,true);
    assert.equal(record.binding.source_head,head);assert.equal(record.artifact_id,42);
    assert.equal(record.zip_sha256,'e'.repeat(64));
    assert.deepEqual(Object.keys(record.payloads).sort(),['manifest.json','marker.json']);
  } finally {fs.rmSync(root,{recursive:true,force:true});}
});
