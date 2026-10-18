import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, writeFile, readFile, rm, chmod } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import * as api from '../src/index.js';

const python = fileURLToPath(new URL('../python/.venv/bin/python', import.meta.url));
const schema = { type: 'object', properties: { answer: { type: 'string' } }, required: ['answer'], additionalProperties: false };
const request = { provider: 'pi', prompt: '你好 🌍', schema };

async function fixture(t, behavior = '') {
  const root = await mkdtemp(join(tmpdir(), 'integrator session '));
  t.after(() => rm(root, { recursive: true, force: true }));
  const script = join(root, 'provider.mjs');
  await writeFile(script, `import fs from 'node:fs';
const prompt = fs.readFileSync(0, 'utf8');
if (process.env.LLM_API_KEY) process.exit(7);
fs.appendFileSync(process.argv[1] + '.calls', 'call\\n');
${behavior}
console.log(JSON.stringify({answer: process.env.MYOWN_LLM_MODEL || process.env.MIROFISH_AGENT_MODEL || '你好 🌍'}));
`);
  const launches = join(root, 'launches');
  const launcher = join(root, 'python-launcher');
  await writeFile(launcher, `#!${python}
import os, sys
with open(${JSON.stringify(launches)}, 'a') as log:
    log.write(str(os.getpid()) + '\\n')
os.execv(${JSON.stringify(python)}, [${JSON.stringify(python)}, *sys.argv[1:]])
`);
  await chmod(launcher, 0o755);
  return { root, script, launches, python: launcher, env: {
    ...process.env, AGENT_COMMAND_JSON: JSON.stringify([process.execPath, script]),
    AGENT_LOCK_PATH: join(root, 'requests.lock'), LLM_API_KEY: 'private-secret',
  } };
}

function session(t, options) {
  assert.equal(typeof api.createAgentSession, 'function', 'session API is exported');
  const value = api.createAgentSession(options);
  t.after(() => value.close());
  return value;
}

async function waitForFile(path) {
  for (let i = 0; i < 200; i++) {
    try { return await readFile(path, 'utf8'); } catch (error) { if (error.code !== 'ENOENT') throw error; }
    await delay(25);
  }
  assert.fail(`Fixture did not create ${path}`);
}

async function assertGone(pid) {
  for (let i = 0; i < 100; i++) {
    try { process.kill(pid, 0); } catch (error) { if (error.code === 'ESRCH') return; throw error; }
    await delay(20);
  }
  assert.fail(`Process ${pid} survived cleanup`);
}

test('warm starts one real Python runtime without inference and sequential calls reuse it', async t => {
  const f = await fixture(t);
  const agent = session(t, f);
  const warm = await agent.warm();
  assert.ok(warm.startupMs > 0);
  await assert.rejects(readFile(f.script + '.calls'), { code: 'ENOENT' });
  assert.deepEqual(await agent.warm(), { startupMs: 0 });
  for (const model of ['first', 'second']) {
    const answer = await agent.runAgent({ ...request, model });
    assert.deepEqual(answer.result, { answer: model });
    assert.deepEqual(answer.usage, {});
    assert.equal(answer.timings.startupMs, 0);
    assert.equal(answer.timings.reusedRuntime, true);
    assert.ok(answer.timings.inferenceMs >= 0);
    assert.ok(answer.timings.totalMs >= answer.timings.inferenceMs);
  }
  assert.equal((await readFile(f.launches, 'utf8')).trim().split('\n').length, 1);
});

test('cold calls report startup and snapshot environment through idle crash recovery', async t => {
  const f = await fixture(t);
  f.env.AGENT_MODEL = 'snapshot';
  const agent = session(t, f);
  f.env.AGENT_MODEL = 'mutated';
  const first = await agent.runAgent(request);
  assert.equal(first.result.answer, 'snapshot');
  assert.equal(first.timings.reusedRuntime, false);
  assert.ok(first.timings.startupMs > 0);
  assert.ok(first.timings.totalMs >= first.timings.startupMs + first.timings.inferenceMs);
  const pid = Number((await readFile(f.launches, 'utf8')).trim());
  process.kill(pid, 'SIGKILL');
  await assertGone(pid);
  await delay(30);
  const next = await agent.runAgent(request);
  assert.equal(next.result.answer, 'snapshot');
  assert.equal(next.timings.reusedRuntime, false);
  assert.equal((await readFile(f.launches, 'utf8')).trim().split('\n').length, 2);
});

test('concurrent warm and inference calls reject instead of queuing', async t => {
  const f = await fixture(t, `await new Promise(resolve => setTimeout(resolve, 200));`);
  const agent = session(t, f);
  const warming = agent.warm();
  await assert.rejects(agent.warm(), error => error instanceof api.ProviderError && /busy|progress/i.test(error.message));
  await assert.rejects(agent.runAgent(request), api.ProviderError);
  await warming;
  const running = agent.runAgent(request);
  await assert.rejects(agent.runAgent(request), api.ProviderError);
  await assert.rejects(agent.warm(), api.ProviderError);
  await running;
});

test('invalid request and schema fail before provider invocation and preserve a healthy session', async t => {
  const f = await fixture(t);
  const agent = session(t, f);
  const cyclic = {}; cyclic.self = cyclic;
  for (const invalid of [
    {}, { ...request, prompt: 1 }, { ...request, schema: [] },
    { ...request, schema: cyclic }, { ...request, provider: '' },
    { ...request, model: 4 }, { ...request, model: '' },
    { ...request, python: '/untrusted' }, { ...request, env: {} },
    { ...request, timeoutMs: 0 }, { ...request, signal: {} },
    { ...request, prompt: 'x'.repeat(8 * 1024 * 1024) },
  ]) await assert.rejects(agent.runAgent(invalid), api.ProviderError);
  await assert.rejects(readFile(f.launches), { code: 'ENOENT' });
  await agent.warm();
  await assert.rejects(agent.runAgent({ ...request, schema: { type: 42 } }), api.ProviderError);
  await assert.rejects(readFile(f.script + '.calls'), { code: 'ENOENT' });
  const answer = await agent.runAgent(request);
  assert.equal(answer.result.answer, '你好 🌍');
  assert.equal(answer.timings.reusedRuntime, true);
});

test('cancellation waits for provider termination and the next call starts a fresh runtime', async t => {
  const f = await fixture(t, `if (prompt.includes('wait:forever')) {
    fs.writeFileSync(process.argv[1] + '.pid', String(process.pid));
    await new Promise(resolve => setTimeout(resolve, 30000));
  }`);
  const agent = session(t, f);
  const controller = new AbortController();
  const pending = agent.runAgent({ ...request, prompt: 'wait:forever', signal: controller.signal });
  const rejected = assert.rejects(pending, error => error instanceof api.ProviderError && /cancelled/i.test(error.message));
  const pid = Number(await waitForFile(f.script + '.pid'));
  controller.abort();
  await rejected;
  await assertGone(pid);
  const result = await agent.runAgent(request);
  assert.equal(result.timings.reusedRuntime, false);
  assert.equal(result.result.answer, '你好 🌍');
});

test('timeouts and close stop active inference; closed sessions stay closed', async t => {
  const f = await fixture(t, `if (prompt.includes('wait:forever')) {
    fs.writeFileSync(process.argv[1] + '.pid', String(process.pid));
    await new Promise(resolve => setTimeout(resolve, 30000));
  }`);
  const agent = session(t, { ...f, timeoutMs: 2000 });
  await agent.warm();
  await assert.rejects(agent.runAgent({ ...request, prompt: 'wait:forever', timeoutMs: 250 }), /timed out/i);
  await assertGone(Number(await waitForFile(f.script + '.pid')));
  const pending = agent.runAgent({ ...request, prompt: 'wait:forever' });
  const rejected = assert.rejects(pending, /closed/i);
  await delay(150);
  await Promise.all([agent.close(), agent.close()]);
  await rejected;
  await assert.rejects(agent.runAgent(request), /closed/i);
  await assert.rejects(agent.warm(), /closed/i);
});

test('provider failure is sanitized, is not retried, and leaves the session reusable', async t => {
  const f = await fixture(t, `if (prompt.includes('bad:output')) { console.error('private-secret'); console.log('private-secret'); process.exit(0); }`);
  const agent = session(t, f);
  await assert.rejects(agent.runAgent({ ...request, prompt: 'bad:output' }), error =>
    error instanceof api.ProviderError && !error.message.includes('private-secret'));
  assert.equal((await readFile(f.script + '.calls', 'utf8')).trim(), 'call');
  assert.equal((await agent.runAgent(request)).timings.reusedRuntime, true);
});

async function protocolFixture(t, body) {
  const root = await mkdtemp(join(tmpdir(), 'session protocol '));
  t.after(() => rm(root, { recursive: true, force: true }));
  const script = join(root, 'runtime');
  await writeFile(script, `#!${python}
import json, os, signal, sys, time
${body}
`);
  await chmod(script, 0o755);
  return session(t, { python: script, timeoutMs: 2000 });
}

test('JSONL decoding preserves UTF-8 characters split between stdout chunks', async t => {
  const agent = await protocolFixture(t, `print(json.dumps({'type':'ready','protocol':1}), flush=True)
for line in sys.stdin:
    request = json.loads(line)
    response = json.dumps({'id':request['id'],'ok':True,'value':{'result':{'answer':'你好 🌍'},'usage':{}},'inferenceMs':0}, ensure_ascii=False).encode() + b'\\n'
    start = response.index('你'.encode())
    for part in (response[:start + 1], response[start + 1:start + 2], response[start + 2:]):
        os.write(1, part)
        time.sleep(.02)`);
  assert.deepEqual((await agent.runAgent(request)).result, { answer: '你好 🌍' });
});

test('malformed protocol IDs, reply shapes, UTF-8, and oversized frames reject without leaking output', async t => {
  for (const body of [
    `print(json.dumps({'id':'wrong-id','ok':True,'value':{'result':'private-secret','usage':{}},'inferenceMs':0}), flush=True)`,
    `print(json.dumps({'id':request['id'],'ok':True,'value':{'result':'private-secret','usage':{}},'inferenceMs':-1}), flush=True)`,
    `print(json.dumps({'id':request['id'],'ok':True,'value':{'result':'private-secret'},'inferenceMs':0}), flush=True)`,
    `os.write(1, b'\\xff\\n')`,
    `os.write(1, b'x' * (32 * 1024 * 1024 + 1))`,
  ]) {
    const agent = await protocolFixture(t, `print(json.dumps({'type':'ready','protocol':1}), flush=True)
request = json.loads(sys.stdin.readline())
${body}
time.sleep(30)`);
    await assert.rejects(agent.runAgent(request), error =>
      error instanceof api.ProviderError && !error.message.includes('private-secret'));
    await agent.close();
  }
});

test('warm cancellation and SIGKILL escalation reap runtimes that ignore SIGTERM', async t => {
  const f = await fixture(t);
  const pidPath = join(f.root, 'waiting-runtime.pid');
  const agent = await protocolFixture(t, `signal.signal(signal.SIGTERM, signal.SIG_IGN)
with open(${JSON.stringify(pidPath)}, 'w') as log:
    log.write(str(os.getpid()))
time.sleep(30)`);
  const controller = new AbortController();
  const pending = agent.warm({ signal: controller.signal });
  const rejected = assert.rejects(pending, /cancelled/i);
  const pid = Number(await waitForFile(pidPath));
  controller.abort();
  await rejected;
  await assertGone(pid);
  await assert.rejects(agent.warm({ signal: controller.signal }), /cancelled/i);
});

test('close kills descendants even when the runtime exits before its process group', async t => {
  const f = await fixture(t);
  const pidPath = join(f.root, 'descendant.pid');
  const agent = await protocolFixture(t, `import subprocess
child = subprocess.Popen([sys.executable, '-c', 'import os, signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print(os.getpid(), flush=True); time.sleep(30)'], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
with open(${JSON.stringify(pidPath)}, 'w') as log:
    log.write(child.stdout.readline().decode())
print(json.dumps({'type':'ready','protocol':1}), flush=True)
time.sleep(30)`);
  await agent.warm();
  const pid = Number(await readFile(pidPath, 'utf8'));
  t.after(() => { try { process.kill(pid, 'SIGKILL'); } catch {} });
  await agent.close();
  await assertGone(pid);
});

test('a session parent exiting stops both its persistent runtime and active provider', async t => {
  const f = await fixture(t, `fs.writeFileSync(process.argv[1] + '.pid', String(process.pid)); await new Promise(resolve => setTimeout(resolve, 30000));`);
  const entry = new URL('../src/index.js', import.meta.url).href;
  const child = spawn(process.execPath, ['--input-type=module', '-e',
    `import {createAgentSession} from ${JSON.stringify(entry)};
const agent = createAgentSession({python:${JSON.stringify(f.python)}});
await agent.runAgent({provider:'pi',prompt:'hello',schema:{}});`],
  { env: f.env, stdio: 'ignore' });
  t.after(() => child.kill('SIGKILL'));
  const providerPid = Number(await waitForFile(f.script + '.pid'));
  const runtimePid = Number((await readFile(f.launches, 'utf8')).trim());
  const exited = once(child, 'exit');
  child.kill('SIGTERM');
  await exited;
  await assertGone(providerPid);
  await assertGone(runtimePid);
});

test('failed runtime launch uses ProviderError and remains restartable', async t => {
  const agent = session(t, { python: '/does/not/exist/python' });
  for (let i = 0; i < 2; i++) {
    await assert.rejects(agent.warm(), error => error instanceof api.ProviderError && /start/i.test(error.message));
  }
});
