import { spawn } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { ProviderError } from './index.js';

const project = fileURLToPath(new URL('../python/', import.meta.url));
const bridge = fileURLToPath(new URL('../python/session_bridge.py', import.meta.url));
const MAX_INPUT = 8 * 1024 * 1024;
const MAX_OUTPUT = 32 * 1024 * 1024;
const DEFAULT_TIMEOUT = 900_000;
const RUNTIME_FAILED = 'Inference session runtime failed. Check Python dependencies and CLI configuration.';

function object(value) { return value !== null && typeof value === 'object' && !Array.isArray(value); }
function keys(value, expected) {
  return object(value) && Object.keys(value).length === expected.length && expected.every(key => Object.hasOwn(value, key));
}
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
function timeout(value) {
  if (!Number.isFinite(value) || value <= 0) throw new ProviderError('timeoutMs must be positive.');
  return value;
}
function callOptions(options, allowed, defaultTimeout) {
  if (!object(options) || Object.keys(options).some(key => !allowed.includes(key))) {
    throw new ProviderError('Invalid session call options. Set python, uv, and env when creating the session.');
  }
  const { signal } = options;
  if (signal !== undefined && (!signal || typeof signal.aborted !== 'boolean' ||
      typeof signal.addEventListener !== 'function' || typeof signal.removeEventListener !== 'function')) {
    throw new ProviderError('signal must be an AbortSignal.');
  }
  if (signal?.aborted) throw new ProviderError('Inference request cancelled.');
  return { signal, timeoutMs: timeout(options.timeoutMs ?? defaultTimeout) };
}
function serializeRequest(options) {
  const { prompt, schema, provider, model } = options;
  if (typeof prompt !== 'string' || !object(schema)) {
    throw new ProviderError('Expected a prompt string and a JSON Schema object.');
  }
  if (provider !== undefined && (typeof provider !== 'string' || !provider.trim())) {
    throw new ProviderError('Provider must be a nonempty string.');
  }
  if (model !== undefined && model !== null && (typeof model !== 'string' || !model.trim())) {
    throw new ProviderError('Model must be a nonempty string or null.');
  }
  const id = randomUUID();
  let input;
  try {
    input = JSON.stringify({ id, operation: 'run', prompt, schema,
      ...(provider !== undefined ? { provider } : {}), ...(model !== undefined ? { model } : {}) }) + '\n';
    if (!object(JSON.parse(input).schema)) throw new Error();
  } catch {
    throw new ProviderError('Inference request must be JSON serializable with a JSON Schema object.');
  }
  if (Buffer.byteLength(input, 'utf8') > MAX_INPUT) {
    throw new ProviderError('Inference request exceeded the input limit.');
  }
  return { id, input };
}

/** Reuse one isolated Python runtime; each call still starts a fresh provider CLI. */
export function createAgentSession(options = {}) {
  if (!object(options)) throw new ProviderError('Expected session runtime options.');
  const { python, uv = 'uv', env = process.env, timeoutMs = DEFAULT_TIMEOUT } = options;
  const defaultTimeout = timeout(timeoutMs);
  if ((python !== undefined && (typeof python !== 'string' || !python.trim())) || typeof uv !== 'string' || !uv.trim()) {
    throw new ProviderError('python and uv must be nonempty executable paths.');
  }
  if (!object(env) || Object.values(env).some(value => value !== undefined && typeof value !== 'string')) {
    throw new ProviderError('env must map environment names to strings or undefined.');
  }
  // An idle runtime and every cold restart use the configuration from creation.
  const environment = { ...env };
  const interpreter = python || environment.MYOWN_LLM_PYTHON;
  let runtime = null, active = null, closed = false, closing;

  function kill(state, signalName) {
    try { if (state.child.pid) process.kill(-state.child.pid, signalName); }
    catch (error) {
      if (error.code !== 'ESRCH') {
        try { state.child.kill(signalName); } catch { /* Already gone. */ }
      }
    }
  }

  function groupExists(state) {
    if (!state.child.pid) return false;
    try { process.kill(-state.child.pid, 0); return true; }
    catch (error) { return error.code !== 'ESRCH'; }
  }

  function finish(state) {
    clearTimeout(state.killTimer);
    if (runtime === state) runtime = null;
    const failure = state.failure || new ProviderError(RUNTIME_FAILED);
    state.failure = failure;
    state.ready.reject(failure);
    state.pending?.reject(failure);
    state.pending = null;
    state.frame = '';
    state.done.resolve();
  }

  function stop(state, failure) {
    if (state.closed) return state.done.promise;
    if (!state.failure) {
      state.failure = failure;
      kill(state, 'SIGTERM');
      state.killTimer = setTimeout(() => {
        state.escalated = true;
        kill(state, 'SIGKILL');
        if (state.closed) finish(state);
      }, 1000);
    }
    return state.done.promise;
  }

  function receive(state, response) {
    if (!state.isReady) {
      if (!keys(response, ['type', 'protocol']) || response.type !== 'ready' || response.protocol !== 1) throw new Error();
      state.isReady = true;
      state.startupMs = performance.now() - state.started;
      state.ready.resolve();
      return;
    }
    const pending = state.pending;
    if (!pending || !object(response) || response.id !== pending.id) throw new Error();
    if (response.ok === true) {
      if (!keys(response, ['id', 'ok', 'value', 'inferenceMs']) ||
          !keys(response.value, ['result', 'usage']) || !object(response.value.usage) ||
          !Number.isFinite(response.inferenceMs) || response.inferenceMs < 0) throw new Error();
    } else if (response.ok !== false || !keys(response, ['id', 'ok', 'error']) ||
               typeof response.error !== 'string' || !response.error.trim()) {
      throw new Error();
    }
    state.pending = null;
    pending.resolve(response);
  }

  function consume(state, chunk) {
    if (state.failure) return;
    try {
      let offset = 0;
      while (offset < chunk.length) {
        const newline = chunk.indexOf(10, offset);
        const end = newline < 0 ? chunk.length : newline + 1;
        state.frameBytes += end - offset;
        if (state.frameBytes > MAX_OUTPUT) {
          stop(state, new ProviderError('Inference response exceeded the output limit.'));
          return;
        }
        state.frame += state.decoder.decode(chunk.subarray(offset, end), { stream: true });
        offset = end;
        if (newline >= 0) {
          state.frame += state.decoder.decode();
          receive(state, JSON.parse(state.frame));
          state.frame = '';
          state.frameBytes = 0;
        }
      }
    } catch {
      stop(state, new ProviderError('Invalid inference session protocol response.'));
    }
  }

  function start() {
    const started = performance.now();
    const args = interpreter ? ['-I', bridge, String(process.pid)] :
      ['run', '--no-config', '--project', project, '--frozen', '--no-dev', 'python', '-I', bridge, String(process.pid)];
    let child;
    try { child = spawn(interpreter || uv, args, { env: environment, stdio: ['pipe', 'pipe', 'pipe'], detached: true }); }
    catch { throw new ProviderError('Cannot start inference runtime. Install uv/Python or set the python option.'); }
    const state = { child, started, ready: deferred(), done: deferred(), pending: null,
      isReady: false, closed: false, failure: null, frame: '', frameBytes: 0,
      decoder: new TextDecoder('utf-8', { fatal: true }) };
    runtime = state;
    child.stdout.on('data', chunk => consume(state, chunk));
    child.stdout.on('error', () => stop(state, new ProviderError(RUNTIME_FAILED)));
    // Drain logs without retaining or exposing provider output or account data.
    child.stderr.resume();
    child.stderr.on('error', () => {});
    child.stdin.on('error', () => stop(state, new ProviderError(RUNTIME_FAILED)));
    child.once('error', () => stop(state, new ProviderError('Cannot start inference runtime. Install uv/Python or set the python option.')));
    child.once('exit', () => stop(state, new ProviderError(RUNTIME_FAILED)));
    child.once('close', () => {
      state.closed = true;
      // Descendants can close inherited pipes yet survive their parent. Keep
      // escalation alive until the entire original process group is stopped.
      if (state.escalated || !groupExists(state)) finish(state);
    });
    return state;
  }

  async function execute(options, warmOnly) {
    const started = performance.now();
    if (closed) throw new ProviderError('Inference session is closed.');
    if (active) throw new ProviderError('Inference session already has a call in progress.');
    if (process.platform === 'win32') throw new ProviderError('Use macOS, Linux, or WSL for CLI inference.');
    const { signal, timeoutMs: deadlineMs } = callOptions(options,
      warmOnly ? ['signal', 'timeoutMs'] : ['prompt', 'schema', 'provider', 'model', 'signal', 'timeoutMs'], defaultTimeout);
    const request = warmOnly ? null : serializeRequest(options);
    const operation = { state: null, failure: null, done: deferred() };
    active = operation;
    let timer;
    const cancel = message => {
      operation.failure ||= new ProviderError(message);
      if (operation.state) stop(operation.state, operation.failure);
    };
    const abort = () => cancel('Inference request cancelled.');
    const scheduleDeadline = () => {
      const remaining = deadlineMs - (performance.now() - started);
      if (remaining <= 0) cancel('Inference runtime timed out.');
      else timer = setTimeout(scheduleDeadline, Math.min(remaining, 2_147_483_647));
    };
    const check = () => {
      if (operation.failure) throw operation.failure;
      if (operation.state?.failure) throw operation.state.failure;
    };
    signal?.addEventListener('abort', abort, { once: true });
    try {
      if (signal?.aborted) abort();
      scheduleDeadline();
      check();
      // An idle worker may have exited while its stdio was still closing.
      if (runtime && (runtime.failure || runtime.child.exitCode !== null || runtime.child.signalCode !== null)) {
        operation.state = runtime;
        await stop(runtime, new ProviderError(RUNTIME_FAILED));
        operation.state = null;
        check();
      }
      const reusedRuntime = Boolean(runtime?.isReady);
      const state = runtime || start();
      operation.state = state;
      await state.ready.promise;
      check();
      const startupMs = reusedRuntime ? 0 : state.startupMs;
      if (warmOnly) return { startupMs };
      const pending = { ...deferred(), id: request.id };
      state.pending = pending;
      state.child.stdin.write(request.input, error => {
        if (error) stop(state, new ProviderError(RUNTIME_FAILED));
      });
      const response = await pending.promise;
      check();
      if (!response.ok) throw new ProviderError(response.error);
      return { ...response.value, timings: { startupMs, inferenceMs: response.inferenceMs,
        totalMs: performance.now() - started, reusedRuntime } };
    } catch (error) {
      if (operation.state?.failure) await operation.state.done.promise;
      throw operation.failure || (error instanceof ProviderError ? error : new ProviderError(RUNTIME_FAILED));
    } finally {
      clearTimeout(timer);
      signal?.removeEventListener('abort', abort);
      active = null;
      operation.done.resolve();
    }
  }

  return {
    warm(options = {}) { return execute(options, true); },
    runAgent(options) { return execute(options, false); },
    close() {
      if (!closing) {
        closed = true;
        const failure = new ProviderError('Inference session is closed.');
        if (active) active.failure ||= failure;
        const stopped = runtime ? stop(runtime, failure) : Promise.resolve();
        const finished = active?.done.promise;
        closing = Promise.all([stopped, finished]).then(() => {});
      }
      return closing;
    },
  };
}
