import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, writeFile, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { spawn } from 'node:child_process';
import { once } from 'node:events';

const schema = { type: 'object', properties: { answer: { type: 'string' } }, required: ['answer'], additionalProperties: false };

async function fixture(t, behavior = '') {
  const root = await mkdtemp(join(tmpdir(), 'integrator test '));
  t.after(() => rm(root, { recursive: true, force: true }));
  const script = join(root, 'fake provider.mjs');
  await writeFile(script, `import fs from 'node:fs';
    const prompt = fs.readFileSync(0, 'utf8');
    if (process.env.LLM_API_KEY || process.env.LOCAL_GRAPH_TOKEN) process.exit(7);
    ${behavior}
    console.log(JSON.stringify({answer: process.env.MYOWN_LLM_MODEL || process.env.MIROFISH_AGENT_MODEL || 'hello'}));
  `);
  return { root, env: { ...process.env, AGENT_COMMAND_JSON: JSON.stringify([process.execPath, script]),
    AGENT_LOCK_PATH: join(root, 'requests.lock'), LLM_API_KEY: 'private-secret', LOCAL_GRAPH_TOKEN: 'private-secret' } };
}

test('npm API executes the standalone engine and honors a per-request model', async t => {
  const { runAgent } = await import('../src/index.js');
  const { env } = await fixture(t, `if (!prompt.includes('Output JSON Schema') || !prompt.includes('你好')) process.exit(8);`);
  const result = await runAgent({ provider: 'claude', model: 'chosen', prompt: '你好', schema, env });
  assert.deepEqual(result, { result: { answer: 'chosen' }, usage: {} });
  assert.equal(env.AGENT_MODEL, process.env.AGENT_MODEL);
});

test('schema violations and provider secrets never escape as successful results', async t => {
  const { runAgent, ProviderError } = await import('../src/index.js');
  const { env } = await fixture(t, `console.log('private-secret'); process.exit(0);`);
  await assert.rejects(runAgent({ provider: 'pi', prompt: 'hello', schema, env }), error =>
    error instanceof ProviderError && !error.message.includes('private-secret'));
});

test('unknown providers fail without executing the wrapper', async t => {
  const { runAgent } = await import('../src/index.js');
  const { root, env } = await fixture(t);
  await assert.rejects(runAgent({ provider: 'unknown', prompt: 'hello', schema, env }), /Unknown agent provider/);
  await assert.rejects(readFile(join(root, 'requests.lock')), { code: 'ENOENT' });
});

test('abort terminates active inference and releases the request lock', async t => {
  const { runAgent } = await import('../src/index.js');
  const { root, env } = await fixture(t, `fs.writeFileSync(process.argv[1] + '.pid', String(process.pid)); await new Promise(r => setTimeout(r, 30000));`);
  const controller = new AbortController();
  const request = runAgent({ provider: 'pi', prompt: 'hello', schema, env, signal: controller.signal });
  const rejected = assert.rejects(request, /cancelled|abort/i);
  let pid;
  for (let n = 0; n < 200; n++) {
    try { pid = Number(await readFile(join(root, 'fake provider.mjs.pid'), 'utf8')); break; } catch {}
    await delay(50);
  }
  assert.ok(pid, 'provider started');
  controller.abort();
  await rejected;
  for (let n = 0; n < 100; n++) {
    try { process.kill(pid, 0); } catch { return; }
    await delay(20);
  }
  assert.fail('provider survived cancellation');
});

test('an explicit Python interpreter works without uv', async t => {
  const { runAgent } = await import('../src/index.js');
  const { env } = await fixture(t);
  const python = new URL('../python/.venv/bin/python', import.meta.url).pathname;
  const result = await runAgent({ provider: 'pi', prompt: 'hello', schema, env, python });
  assert.equal(result.result.answer, 'hello');
});

test('provider listing and validation do not run inference', async t => {
  const { listProviders, validateProvider } = await import('../src/index.js');
  const { env } = await fixture(t, `throw new Error('Must never execute');`);
  const providers = await listProviders({ env });
  assert.equal(providers.length, 15);
  assert.deepEqual(await validateProvider('claude', { env }), { id: 'claude-code', name: 'Claude Code', executable: 'claude' });
});

test('a Node parent exiting does not leave inference running', async t => {
  const { root, env } = await fixture(t, `fs.writeFileSync(process.argv[1] + '.pid', String(process.pid)); await new Promise(r => setTimeout(r, 30000));`);
  const entry = new URL('../src/index.js', import.meta.url).href;
  const child = spawn(process.execPath, ['--input-type=module', '-e',
    `import {runAgent} from ${JSON.stringify(entry)}; await runAgent({provider:'pi',prompt:'hello',schema:{}});`],
    { env, stdio: 'ignore' });
  t.after(() => child.kill('SIGKILL'));
  let pid;
  for (let n = 0; n < 200; n++) {
    try { pid = Number(await readFile(join(root, 'fake provider.mjs.pid'), 'utf8')); break; } catch {}
    await delay(50);
  }
  assert.ok(pid, 'provider started');
  const exited = once(child, 'exit');
  child.kill('SIGTERM');
  await exited;
  for (let n = 0; n < 100; n++) {
    try { process.kill(pid, 0); } catch { return; }
    await delay(20);
  }
  try { process.kill(pid, 'SIGKILL'); } catch {}
  assert.fail('provider survived its Node parent');
});
