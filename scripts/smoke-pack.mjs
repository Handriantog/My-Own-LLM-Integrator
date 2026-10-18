import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { mkdtemp, writeFile, readFile, chmod, rm, mkdir } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const exec = promisify(execFile);
const source = fileURLToPath(new URL('../', import.meta.url));
const root = await mkdtemp(join(tmpdir(), 'integrator-packed-'));
try {
  const { stdout } = await exec('npm', ['pack', '--json', '--pack-destination', root], { cwd: source });
  const [pack] = JSON.parse(stdout);
  const paths = pack.files.map(file => file.path);
  assert.ok(paths.includes('dist/index.js') && paths.includes('dist/index.d.ts'));
  assert.ok(paths.includes('python/myown_llm_integrator/worker.py'));
  assert.ok(paths.includes('python/session_bridge.py') && paths.includes('dist/session.js'));
  assert.ok(paths.includes('python/provider_bridge.py') && paths.includes('dist/provider-session.js'));
  assert.ok(paths.includes('python/myown_llm_integrator/provider_session.py'));
  assert.ok(paths.includes('python/uv.lock'));
  assert.ok(!paths.some(path => /(^|\/)(\.venv|__pycache__|\.env|node_modules|tests)(\/|$)/.test(path)));
  const consumer = join(root, 'consumer');
  await mkdir(consumer);
  await writeFile(join(consumer, 'package.json'), JSON.stringify({ name: 'independent-consumer', private: true, type: 'module' }));
  await exec('npm', ['install', '--ignore-scripts', '--no-audit', '--no-fund', join(root, pack.filename)], { cwd: consumer });
  const wrapper = join(root, 'fake-provider.mjs');
  await writeFile(wrapper, `import fs from 'node:fs'; const input=fs.readFileSync(0,'utf8');
    if (!input.includes('packed consumer')) process.exit(2);
    console.log(JSON.stringify({answer:'standalone'}));`);
  await writeFile(join(consumer, 'verify.mjs'), `
    import assert from 'node:assert/strict';
    import {runAgent,listProviders,createAgentSession,createProviderSession} from 'myown-llm-integrator';
    const providers=await listProviders(); assert.equal(providers.length,15);
    const result=await runAgent({provider:'pi',prompt:'packed consumer',schema:{type:'object',required:['answer']}});
    assert.deepEqual(result,{result:{answer:'standalone'},usage:{}});
    const session=createAgentSession();
    try {
      await session.warm();
      const reused=await session.runAgent({provider:'pi',prompt:'packed consumer',schema:{type:'object',required:['answer']}});
      assert.deepEqual(reused.result,result.result);
      assert.equal(reused.timings.reusedRuntime,true);
    } finally { await session.close(); }
    const provider=createProviderSession();
    try {
      await provider.warm();
      const spans=[];
      const decision=await provider.runDecision({prompt:'packed authoritative context',toolNames:['lookup'],onText:record=>{spans.push(record);}});
      assert.deepEqual(decision.result,{text:'First sentence. Second sentence.',toolCalls:[]});
      assert.equal(spans.length,2);
      assert.equal(decision.timings.reusedProvider,true);
      assert.ok(decision.timings.phases.firstDeltaMs >= 0);
      assert.ok(decision.timings.phases.firstValidatedTextMs >= decision.timings.phases.firstDeltaMs);
      assert.ok(decision.timings.phases.totalMs <= decision.timings.inferenceMs);
    } finally { await provider.close(); }
    console.log('Packed Node consumer and provider stream passed');
  `);
  const fakeCodex=join(root,'codex');
  const fixtureSource=await readFile(new URL('../tests/fixtures/app-server.py',import.meta.url),'utf8');
  await writeFile(fakeCodex,fixtureSource.replace('#!/usr/bin/env python3',`#!${join(consumer,'node_modules/myown-llm-integrator/python/.venv/bin/python')}`));
  await chmod(fakeCodex,0o755);
  await writeFile(join(root,'options.json'),'{}');
  const env = { ...process.env, CODEX_BIN:fakeCodex, CODEX_LOCK_PATH:join(root,'codex.lock'), AGENT_COMMAND_JSON: JSON.stringify([process.execPath, wrapper]), AGENT_LOCK_PATH: join(root, 'requests.lock') };
  const result = await exec(process.execPath, ['verify.mjs'], { cwd: consumer, env, timeout: 120_000 });
  console.log(result.stdout.trim());
  const python = join(consumer, 'node_modules/myown-llm-integrator/python/.venv/bin/python');
  await exec(python, ['-I', '-c', 'import myown_llm_integrator; assert len(myown_llm_integrator.PROVIDERS) == 15'], { cwd: root });
  const cli = await exec(process.execPath, ['node_modules/myown-llm-integrator/dist/cli.js', 'providers'], { cwd: consumer });
  assert.equal(JSON.parse(cli.stdout).length, 15);
  console.log(`Packed Python consumer and CLI passed; ${paths.length} package files inspected`);
} finally {
  await rm(root, { recursive: true, force: true });
}
