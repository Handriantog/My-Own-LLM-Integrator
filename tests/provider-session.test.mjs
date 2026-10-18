import test from 'node:test';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import assert from 'node:assert/strict';
import { mkdtemp, writeFile, readFile, rm, chmod } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';
import * as api from '../src/index.js';
const python=fileURLToPath(new URL('../python/.venv/bin/python',import.meta.url));
async function fixture(t, options={}) {
  const root=await mkdtemp(join(tmpdir(),'provider-session-'));
  t.after(()=>rm(root,{recursive:true,force:true}));
  const script=join(root,'codex');
  const source=await readFile(new URL('./fixtures/app-server.py',import.meta.url),'utf8');
  await writeFile(script,source.replace('#!/usr/bin/env python3',`#!${python}`));
  await chmod(script,0o755);
  await writeFile(join(root,'options.json'),JSON.stringify(options));
  assert.equal(typeof api.createProviderSession,'function','public provider session export');
  const session=api.createProviderSession({python,env:{...process.env,CODEX_BIN:script,CODEX_LOCK_PATH:join(root,'requests.lock'),OPENAI_API_KEY:'secret',APP_SECRET:'secret'},timeoutMs:6000});
  t.after(()=>session.close());
  return {root,session,logs:async()=> (await readFile(join(root,'requests.jsonl'),'utf8')).trim().split('\n').map(JSON.parse)};
}
const request={prompt:'authoritative full context',toolNames:['lookup'],model:'selected-quality-model'};
test('provider warms without a turn, streams before completion, awaits callbacks and reuses the provider with fresh decision contexts',async t=>{
 const f=await fixture(t);const warm=await f.session.warm();assert.ok(warm.startupMs>0);
 assert.ok(!(await f.logs()).some(x=>x.method==='turn/start'));
 const spans=[]; let done=false; let release; const gate=new Promise(r=>release=r);
 const result=f.session.runDecision({...request,onText:async span=>{spans.push(span);if(span.sequence===0)await gate;}}).then(x=>{done=true;return x;});
 for(let i=0;i<100 && spans.length===0;i++)await delay(10);
 assert.deepEqual(spans,[{sequence:0,text:'First sentence. '}]);assert.equal(done,false);
 await delay(350);assert.equal(spans.length,1);release();
 const answer=await result;
 assert.ok(answer.timings.phases.textCallbackMs >= 300);
 assert.ok(answer.timings.phases.firstValidatedTextMs < answer.timings.phases.turnCompletedMs);
 assert.deepEqual(answer.result,{text:'First sentence. Second sentence.',toolCalls:[]});
 assert.equal(answer.timings.reusedProvider,true); assert.ok(answer.timings.firstTextMs<answer.timings.totalMs);
 assert.deepEqual(spans,[{sequence:0,text:'First sentence. '},{sequence:1,text:'Second sentence.'}]);
 const next=await f.session.runDecision({...request,prompt:'second authoritative snapshot'});
 assert.equal(next.timings.reusedProvider,true);
 const logs=await f.logs();assert.equal(logs.filter(x=>x.method==='thread/start').length,2);
 assert.ok(logs.filter(x=>x.spawn).every(x=>x.cwdEmpty));
 const turns=logs.filter(x=>x.method==='turn/start');assert.equal(turns.length,2);
 assert.equal(turns[0].params.model,'selected-quality-model');
 assert.ok(turns[0].params.input[0].text.includes(request.prompt));
 assert.ok(turns[1].params.input[0].text.includes('second authoritative snapshot'));
});
test('tools remain data and return only after successful completion',async t=>{
 const f=await fixture(t);const spans=[];
 const value=await f.session.runDecision({...request,prompt:'TOOLS',onText:s=>spans.push(s)});
 assert.deepEqual(value.result,{text:'',toolCalls:[{name:'lookup',argumentsJson:'{}'}]});assert.deepEqual(spans,[]);
 assert.equal(value.timings.phases.firstValidatedTextMs,null);
 assert.ok(value.timings.phases.firstDeltaMs >= 0);
 await assert.rejects(f.session.runDecision({...request,prompt:'TOOLS FAIL'}));
});
for(const behavior of ['MIXED','MALFORMED','FAIL']) test(`${behavior} retains accepted records and discards provider state before next explicit call`,async t=>{
 const f=await fixture(t);const spans=[];
 await assert.rejects(f.session.runDecision({...request,prompt:behavior,onText:s=>spans.push(s)}));
 assert.equal(spans[0].text,'First sentence. ');
 const next=await f.session.runDecision(request);assert.equal(next.timings.reusedProvider,false);
 const starts=(await f.logs()).filter(x=>x.method==='thread/start');assert.equal(starts.length,2);assert.notEqual(starts[0].pid,starts[1].pid);
});
for(const behavior of ['COMMENTARY','APPROVAL']) test(`${behavior} never reaches text callback`,async t=>{
 const f=await fixture(t);const spans=[];
 await assert.rejects(f.session.runDecision({...request,prompt:behavior,onText:s=>spans.push(s)}));assert.deepEqual(spans,[]);
});
for(const option of ['apiKey','apiKeyAccount','unsafeConfig'])test(`${option} fails closed before inference`,async t=>{
 const f=await fixture(t,{[option]:true});await assert.rejects(f.session.warm());
 let logs=[];try{logs=await f.logs();}catch{}assert.ok(!logs.some(x=>x.method==='turn/start'||x.method==='thread/start'));
});
test('abort during a never-settling callback cancels promptly and no late callback can affect next request',async t=>{
 const f=await fixture(t);const controller=new AbortController();let entered;
 const ready=new Promise(r=>entered=r);let release;
 const pending=f.session.runDecision({...request,signal:controller.signal,onText:()=>{entered();return new Promise(r=>release=r);}});
 await ready;controller.abort();await assert.rejects(pending,/cancel/);
 const next=f.session.runDecision(request);release();assert.equal((await next).timings.reusedProvider,false);
});
test('callback failure cancels request and is not retried',async t=>{
 const f=await fixture(t);await assert.rejects(f.session.runDecision({...request,onText:()=>{throw Error('private callback detail');}}),/callback/i);
 assert.equal((await f.logs()).filter(x=>x.method==='turn/start').length,1);
});
test('one active request, deadline, and terminal idempotent close',async t=>{
 const f=await fixture(t);await f.session.warm();const pending=f.session.runDecision({...request,prompt:'STALL',timeoutMs:80});
 await assert.rejects(f.session.warm(),/progress/);await assert.rejects(pending,/timed out/);
 await f.session.close();await f.session.close();await assert.rejects(f.session.runDecision(request),/closed/);
});

test('unknown phase is buffered until successful completion and never leaks on failed completion',async t=>{
 const f=await fixture(t);const spans=[];const start=performance.now();
 const value=await f.session.runDecision({...request,prompt:'NULL_PHASE',onText:s=>spans.push({...s,at:performance.now()-start})});
 assert.equal(value.timings.delivery,'buffered');assert.equal(spans.length,2);assert.ok(spans[0].at>=390);
 assert.ok(value.timings.phases.firstDeltaMs < value.timings.phases.turnCompletedMs);
 assert.ok(value.timings.phases.firstValidatedTextMs >= value.timings.phases.turnCompletedMs);
 const failed=[];await assert.rejects(f.session.runDecision({...request,prompt:'NULL_PHASE FAIL',onText:s=>failed.push(s)}));assert.deepEqual(failed,[]);
});

test('invalid requests reject before starting runtime; explicit null model restores CLI default',async t=>{
 const f=await fixture(t);
 for(const bad of [{toolNames:['a','a']},{toolNames:['']},{toolNames:'lookup'},{onText:2},{provider:'pi'},{model:''},{prompt:null}]) {
  await assert.rejects(f.session.runDecision({...request,...bad}));
 }
 await assert.rejects(readFile(join(f.root,'requests.jsonl')),{code:'ENOENT'});
 await f.session.runDecision(request);await f.session.runDecision({...request,model:null});
 const turns=(await f.logs()).filter(x=>x.method==='turn/start');assert.equal(turns[1].params.model,'catalog-default');
});

for(const behavior of ['MALFORMED_PROTOCOL','PROTOCOL_LIMIT','STALE_ID','NATIVE_ITEM','BEFORE_START'])test(`${behavior} is rejected without text or retries`,async t=>{
 const f=await fixture(t);const spans=[];
 await assert.rejects(f.session.runDecision({...request,prompt:behavior,onText:s=>spans.push(s)}));assert.deepEqual(spans,[]);
 assert.equal((await f.logs()).filter(x=>x.method==='turn/start').length,1);
});
test('final item mismatch rejects even when earlier records were accepted',async t=>{
 const f=await fixture(t);const spans=[];
 await assert.rejects(f.session.runDecision({...request,prompt:'MISMATCH',onText:s=>spans.push(s)}),/match/);assert.equal(spans.length,2);
});
test('managed feature requirements fail before thread start',async t=>{
 const f=await fixture(t,{unsafeRequirements:true});await assert.rejects(f.session.warm(),/requirements/);
 assert.ok(!(await f.logs()).some(x=>x.method==='thread/start'));
});
test('unreviewed Codex versions fail closed before launching app-server',async t=>{
 const f=await fixture(t,{version:'99.0.0'});await assert.rejects(f.session.warm(),/version/);
 await assert.rejects(readFile(join(f.root,'requests.jsonl')),{code:'ENOENT'});
});


test('provider requests coordinate with the existing cross-process Codex lock and close reaps providers',async t=>{
 const f=await fixture(t);await f.session.warm();
 const locker=spawn(python,['-c','import fcntl,sys; f=open(sys.argv[1],"w"); fcntl.flock(f,fcntl.LOCK_EX); print("locked",flush=True); sys.stdin.read()',join(f.root,'requests.lock')],{stdio:['pipe','pipe','pipe']});
 t.after(()=>locker.kill());await once(locker.stdout,'data');
 const spans=[];const pending=f.session.runDecision({...request,onText:s=>spans.push(s)});
 await delay(180);assert.deepEqual(spans,[]);assert.ok(!(await f.logs()).some(x=>x.method==='turn/start'));
 locker.stdin.end();const result=await pending;
 assert.ok(result.timings.phases.lockWaitMs >= 100);
 assert.ok(result.timings.phases.totalMs > result.timings.phases.lockWaitMs);
 await f.session.close();
 for(const entry of (await f.logs()).filter(x=>x.spawn)){
   assert.throws(()=>process.kill(entry.spawn,0),{code:'ESRCH'});
 }
});


test('effective restrictions are rechecked before every turn even on a reused thread',async t=>{
 const f=await fixture(t);await f.session.runDecision(request);
 await writeFile(join(f.root,'options.json'),JSON.stringify({unsafeDynamic:true}));
 await assert.rejects(f.session.runDecision(request),/restrictions/);
 assert.equal((await f.logs()).filter(x=>x.method==='turn/start').length,1);
});

test('uses catalog defaults instead of inherited coding model and effort, including model switches', async t => {
 const f=await fixture(t);
 await f.session.runDecision({...request,model:null});
 await f.session.runDecision({...request,model:'selected-quality-model'});
 await f.session.runDecision({...request,model:null});
 const logs=await f.logs();
 const start=logs.find(x=>x.method==='thread/start');
 assert.equal(start.params.model,'catalog-default');
 assert.equal(start.params.config.model_reasoning_effort,'medium');
 assert.equal(start.params.serviceTier,'default');
 const turns=logs.filter(x=>x.method==='turn/start');
 assert.deepEqual(turns.map(x=>[x.params.model,x.params.effort]),[
   ['catalog-default','medium'],['selected-quality-model','high'],['catalog-default','medium']
 ]);
});

test('keeps the provider but isolates full authoritative snapshots into fresh unloaded threads',async t=>{
 const f=await fixture(t);await f.session.warm();
 await f.session.runDecision({...request,prompt:'full snapshot one'});
 const next=await f.session.runDecision({...request,prompt:'full snapshot two'});
 assert.equal(next.timings.reusedProvider,true);
 const logs=await f.logs();const starts=logs.filter(x=>x.method==='thread/start');
 assert.equal(starts.length,2);
 assert.equal(new Set(starts.map(x=>x.pid)).size,1);
 const turns=logs.filter(x=>x.method==='turn/start');
 assert.notEqual(turns[0].params.threadId,turns[1].params.threadId);
 assert.equal(logs.filter(x=>x.method==='thread/unsubscribe').length,2);
});

test('separates preparation, RPC acknowledgement, raw delta, validated record and cleanup without changing text', async t => {
 const f=await fixture(t,{phaseDelays:true}); await f.session.warm();
 const result=await f.session.runDecision({...request,prompt:'SPLIT_RECORD private input'});
 assert.deepEqual(result.result,{text:'First sentence. Second sentence.',toolCalls:[]});
 const p=result.timings.phases;
 assert.deepEqual(Object.keys(p).sort(), ['lockWaitMs','preparationMs','accountReadMs','requirementsReadMs','configReadMs','threadConfigReadMs','threadCreateMs','turnStartAckMs','firstDeltaMs',
   'firstValidatedTextMs','turnCompletedMs','textCallbackMs','cleanupMs','totalMs'].sort());
 assert.ok(Object.values(p).every(value=>Number.isFinite(value) && value>=0));
 assert.ok(p.accountReadMs>=25);
 assert.ok(p.requirementsReadMs>=35);
 assert.ok(p.configReadMs>=45);
 assert.ok(p.threadConfigReadMs>=45);
 assert.ok(p.threadCreateMs>=50);
 assert.ok(p.preparationMs >= p.accountReadMs+p.requirementsReadMs+p.configReadMs+p.threadConfigReadMs+p.threadCreateMs);
 assert.ok(p.turnStartAckMs>=40);
 assert.ok(p.firstDeltaMs-p.turnStartAckMs>=65);
 assert.ok(p.firstValidatedTextMs-p.firstDeltaMs>=100);
 assert.ok(p.cleanupMs>=40);
 assert.ok(p.totalMs>=p.lockWaitMs+p.preparationMs+p.turnCompletedMs+p.cleanupMs);
 assert.ok(result.timings.inferenceMs>=p.totalMs);
 assert.ok(!JSON.stringify(p).includes('private'));
});

test('phase metadata accepts only its exact numeric contract and never returns extra private fields', async t => {
 const root=await mkdtemp(join(tmpdir(),'provider-phases-wire-'));
 t.after(()=>rm(root,{recursive:true,force:true}));
 const launcher=join(root,'python');
 await writeFile(launcher,`#!${python}
import json,os,sys
print(json.dumps({'type':'ready','protocol':3}),flush=True)
for line in sys.stdin:
    request=json.loads(line)
    print(json.dumps({'id':request['id'],'ok':True,'value':{'result':{'text':'','toolCalls':[]},'usage':{}},'inferenceMs':1,'delivery':'incremental','phases':json.loads(os.environ['FIXTURE_PHASES'])}),flush=True)
`);
 await chmod(launcher,0o755);
 const valid={lockWaitMs:0,preparationMs:0,accountReadMs:0,requirementsReadMs:0,configReadMs:0,threadConfigReadMs:0,threadCreateMs:0,turnStartAckMs:0,firstDeltaMs:null,
   firstValidatedTextMs:null,turnCompletedMs:0,textCallbackMs:0,cleanupMs:0,totalMs:1};
 for(const phases of [valid,{...valid,lockWaitMs:-1},{...valid,preparationMs:null},
   {...valid,firstDeltaMs:'private'},{...valid,secret:'private'},{...valid,cleanupMs:true}]) {
   const session=api.createProviderSession({python:launcher,env:{...process.env,FIXTURE_PHASES:JSON.stringify(phases)},timeoutMs:2000});
   try {
     if(phases===valid) assert.deepEqual((await session.runDecision(request)).timings.phases,valid);
     else await assert.rejects(session.runDecision(request),/Invalid inference session protocol/);
   } finally {await session.close();}
 }
});
