import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const project = fileURLToPath(new URL('../python/', import.meta.url));
const bridge = fileURLToPath(new URL('../python/bridge.py', import.meta.url));
const MAX_OUTPUT = 32 * 1024 * 1024;

export class ProviderError extends Error {
  constructor(message) { super(message); this.name = 'ProviderError'; }
}

function invoke(request, { signal, python, uv = 'uv', env = process.env, timeoutMs = 900_000 } = {}) {
  if (process.platform === 'win32') return Promise.reject(new ProviderError('Use macOS, Linux, or WSL for CLI inference.'));
  if (signal?.aborted) return Promise.reject(new ProviderError('Inference request cancelled.'));
  if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) return Promise.reject(new ProviderError('timeoutMs must be positive.'));
  let input;
  try { input = JSON.stringify(request); }
  catch { return Promise.reject(new ProviderError('Inference request must be JSON serializable.')); }
  return new Promise((resolve, reject) => {
    const interpreter = python || env.MYOWN_LLM_PYTHON;
    const args = interpreter ? ['-I', bridge, String(process.pid)] :
      ['run', '--no-config', '--project', project, '--frozen', '--no-dev', 'python', '-I', bridge, String(process.pid)];
    const child = spawn(interpreter || uv, args, { env, stdio: ['pipe', 'pipe', 'pipe'], detached: true });
    let output = '', size = 0, failure, killTimer;
    const kill = (signalName) => {
      try { if (child.pid) process.kill(-child.pid, signalName); }
      catch (error) { if (error.code !== 'ESRCH') child.kill(signalName); }
    };
    const stop = message => {
      if (failure) return;
      failure = new ProviderError(message);
      kill('SIGTERM');
      killTimer = setTimeout(() => kill('SIGKILL'), 1000);
      killTimer.unref();
    };
    const abort = () => stop('Inference request cancelled.');
    signal?.addEventListener('abort', abort, { once: true });
    if (signal?.aborted) abort();
    const timer = setTimeout(() => stop('Inference runtime timed out.'), timeoutMs);
    const cleanup = () => {
      clearTimeout(timer); clearTimeout(killTimer);
      signal?.removeEventListener('abort', abort);
    };
    child.stdout.on('data', chunk => {
      size += chunk.length;
      if (size > MAX_OUTPUT) stop('Inference response exceeded the output limit.');
      else output += chunk.toString('utf8');
    });
    // Drain stderr, but never return prompts, account details, or raw provider logs.
    child.stderr.resume();
    child.stdin.on('error', () => {}); // early child exit may close the input pipe
    child.once('error', () => {
      cleanup(); reject(new ProviderError('Cannot start inference runtime. Install uv/Python or set the python option.'));
    });
    child.once('close', code => {
      cleanup();
      if (failure) return reject(failure);
      try {
        const response = JSON.parse(output);
        if (response.ok === false && typeof response.error === 'string') return reject(new ProviderError(response.error));
        if (code !== 0 || response.ok !== true || !Object.hasOwn(response, 'value')) throw new Error();
        resolve(response.value);
      } catch {
        reject(new ProviderError('Inference runtime failed. Check Python dependencies and CLI configuration.'));
      }
    });
    child.stdin.end(input);
  });
}

/** Return schema-validated output from a locally authenticated CLI. */
export function runAgent(options) {
  const { prompt, schema, provider, model, ...runtime } = options ?? {};
  if (typeof prompt !== 'string' || !schema || typeof schema !== 'object' || Array.isArray(schema)) {
    return Promise.reject(new ProviderError('Expected a prompt string and a JSON Schema object.'));
  }
  return invoke({ operation: 'run', prompt, schema, provider, ...(model !== undefined ? { model } : {}) }, runtime);
}

/** List presets without invoking a provider or accessing credentials. */
export function listProviders(options) { return invoke({ operation: 'providers' }, options); }

/** Check executable/configuration only; this does not run inference or verify login. */
export function validateProvider(provider, options) { return invoke({ operation: 'validate', provider }, options); }

export { createAgentSession } from './session.js';

export { createProviderSession } from './provider-session.js';
