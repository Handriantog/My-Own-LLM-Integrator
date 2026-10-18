// Offline bridge-overhead comparison. This never calls an AI account.
import assert from 'node:assert/strict';
import { mkdtemp, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { performance } from 'node:perf_hooks';
import { runAgent, createAgentSession } from '../src/index.js';

const root = await mkdtemp(join(tmpdir(), 'integrator-latency-'));
let session;
try {
  const fixture = join(root, 'provider.mjs');
  await writeFile(fixture, `import fs from 'node:fs';
    fs.readFileSync(0, 'utf8');
    console.log(JSON.stringify({answer:'fixture reply'}));`);
  const env = { ...process.env,
    AGENT_COMMAND_JSON: JSON.stringify([process.execPath, fixture]),
    AGENT_LOCK_PATH: join(root, 'request.lock'),
  };
  const input = { provider: 'pi', prompt: 'Fixture benchmark only.', schema: {
    type: 'object', properties: { answer: { type: 'string' } },
    required: ['answer'], additionalProperties: false,
  } };
  session = createAgentSession({ env });
  const warmup = await session.warm();
  const cold = [], warm = [], engine = [];
  // Alternate paths so scheduling and filesystem caches affect both samples.
  for (let n = 0; n < 10; n++) {
    let start = performance.now();
    const legacy = await runAgent({ ...input, env });
    cold.push(performance.now() - start);
    start = performance.now();
    const reused = await session.runAgent(input);
    warm.push(performance.now() - start);
    engine.push(reused.timings.inferenceMs);
    assert.deepEqual(reused.result, legacy.result);
    assert.deepEqual(reused.usage, legacy.usage);
    assert.equal(reused.timings.reusedRuntime, true);
    assert.equal(reused.timings.startupMs, 0);
  }
  const stats = values => {
    const sorted = [...values].sort((a, b) => a - b);
    return { medianMs: +( (sorted[4] + sorted[5]) / 2 ).toFixed(2),
      minMs: +sorted[0].toFixed(2), maxMs: +sorted.at(-1).toFixed(2) };
  };
  console.log(JSON.stringify({
    at: new Date().toISOString(), platform: `${process.platform}/${process.arch}`,
    node: process.version, samplesPerPath: 10, provider: 'local fixture only',
    liveInference: false, defaultUvRuntime: true, warmup,
    legacy: stats(cold), warmSession: stats(warm), engine: stats(engine),
    note: 'Measures local process/import overhead, not subscription response time or speech latency.',
  }, null, 2));
} finally {
  await session?.close();
  await rm(root, { recursive: true, force: true });
}
