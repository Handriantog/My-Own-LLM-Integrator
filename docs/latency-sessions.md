# Opt-in warm inference sessions

Version 0.2.0 adds `createAgentSession` alongside `runAgent`. It keeps one Python runtime loaded between requests. The existing one-request API and provider implementation remain unchanged. Consumers must install the new package version explicitly; editing this source repository does not update Dots or Researcher.

```js
import { createAgentSession } from 'myown-llm-integrator';

const session = createAgentSession({ timeoutMs: 180_000 });
try {
  await session.warm(); // Imports the engine; no login check or inference.
  const { result, usage, timings } = await session.runAgent({
    provider: 'codex',
    prompt: 'Explain photosynthesis in one sentence.',
    schema: {
      type: 'object',
      properties: { answer: { type: 'string' } },
      required: ['answer'],
      additionalProperties: false,
    },
    signal: AbortSignal.timeout(180_000),
  });
  console.log(result.answer, timings);
} finally {
  await session.close();
}
```

`warm()` prepares the local bridge without invoking a provider. `runAgent()` warms it automatically if needed. As with the existing API, inference uses the selected provider's account. Explicit `python`, `uv`, and `env` options belong to the session constructor; its environment is copied at creation. Prompt, schema, provider, model, signal, and deadline are supplied for each request. An omitted model inherits configuration; `null` uses the CLI default.

Only one operation may be in flight on a session. Concurrent calls reject instead of silently queuing. Separate sessions still obey the engine's existing cross-process request lock. Cancellation, a deadline, or a protocol failure terminates the runtime and its provider work; the next explicit request can start a fresh runtime. Inference is never automatically retried. `close()` is permanent and should be called when a voice call, application worker, or owner shuts down. Sessions contain no conversation history, result cache, cached login decision, or model state.

## Timing fields

| Field | Meaning |
| --- | --- |
| `startupMs` | Time spent starting and loading this request's Python runtime; zero for reuse |
| `inferenceMs` | Python engine duration, including lock wait, provider startup/authentication, generation, and schema validation |
| `totalMs` | Caller-observed duration including startup and transport |
| `reusedRuntime` | Whether this request used a runtime that was already ready |

`warm()` returns its `startupMs`. The first explicit warm moves initialization out of a later conversational turn; it does not eliminate that cost. Timings contain no prompt or provider text. They are not time-to-first-token or time-to-first-audio measurements.

## Why a voice reply can still take several seconds

The old Node API launches `uv`/Python for each request. Python imports the validation/provider engine, acquires a shared request lock, and starts the provider CLI. The current Codex path also checks ChatGPT login for every request, then starts a fresh ephemeral CLI invocation. The CLI generates a structured response; Integrator waits for a completed turn and validates its final JSON. Dots separately validates the action envelope and waits for its governed conversation to complete before speaking.

This API removes repeated Python startup/import work on warm turns. It deliberately keeps all of the later steps, the selected model, the complete prompt, tool restrictions, and schema checks. It cannot remove network/model generation time or queueing behind another app.

Local capability inspection on October 18, 2026 found Codex CLI `0.157.1` exposes `exec --json` events. That flag alone does not establish a supported stream of final, safe-to-speak text. The existing transport buffers those events and reads a completed structured response. This new API therefore returns one validated result, not speculative partial text.

## Measurement and persistent-provider alternative

Run `node scripts/benchmark-session.mjs` to compare the existing API with warm reuse using a local fixture provider. It alternates ten samples per path, checks identical results and usage, and reports process/import overhead. It never invokes a subscription account. Real subscription and conversational latency require a separate live benchmark.

On October 18, 2026, a network-denied run on this Apple Silicon Mac with Node 24.20.0 measured a 150.53 ms legacy median and 79.30 ms warm-session median across ten samples per path. Initial warm-up took 66.07 ms. The roughly 71 ms saving is local overhead only; it does not establish a five-second improvement in voice response time.

Version 0.2.0 also includes the separate opt-in [`createProviderSession` API](provider-sessions.md). It keeps a restricted Codex provider process alive, creates fresh context per decision, and delivers validated text records incrementally while buffering tool decisions. It preserves cancellation and account/configuration checks. The original text-generation API and the warm-Python API described here retain their existing behavior.
