import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { mkdtemp, writeFile, rm, mkdir } from 'node:fs/promises';
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
    import {runAgent,listProviders} from 'myown-llm-integrator';
    const providers=await listProviders(); assert.equal(providers.length,15);
    const result=await runAgent({provider:'pi',prompt:'packed consumer',schema:{type:'object',required:['answer']}});
    assert.deepEqual(result,{result:{answer:'standalone'},usage:{}});
    console.log('Packed Node consumer passed');
  `);
  const env = { ...process.env, AGENT_COMMAND_JSON: JSON.stringify([process.execPath, wrapper]), AGENT_LOCK_PATH: join(root, 'requests.lock') };
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
