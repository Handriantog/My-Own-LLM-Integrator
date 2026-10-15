#!/usr/bin/env node
import { listProviders, runAgent, validateProvider } from './index.js';

try {
  let result;
  if (process.argv[2] === 'providers') result = await listProviders();
  else if (process.argv[2] === 'check') result = await validateProvider(process.argv[3]);
  else if (process.argv.length === 2) {
    let input = '';
    for await (const chunk of process.stdin) {
      input += chunk;
      if (input.length > 32 * 1024 * 1024) throw new Error();
    }
    const { prompt, schema, provider, model } = JSON.parse(input);
    result = await runAgent({ prompt, schema, provider, model });
  } else {
    console.error('Usage: myown-llm-integrator [providers | check PROVIDER]\nOr pipe {"prompt":"...","schema":{},"provider":"codex"} to stdin.');
    process.exitCode = 2;
  }
  if (result !== undefined) console.log(JSON.stringify(result));
} catch (error) {
  console.error(JSON.stringify({ error: error.name === 'ProviderError' ? error.message : 'Invalid integrator request.' }));
  process.exitCode = 1;
}
