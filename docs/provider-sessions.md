# Call-scoped Codex provider sessions

`createProviderSession` is an additive, opt-in API for keeping one local Codex app-server process alive during a single application conversation/call. `runAgent` and `createAgentSession` retain their existing implementations. The provider session requires the reviewed `codex-cli 0.157.1`; an upgrade fails closed until its capability controls have been reviewed.

```js
import { createProviderSession } from 'myown-llm-integrator';

const session = createProviderSession({ timeoutMs: 180_000 });
try {
  await session.warm(); // Configuration/auth checks only; no inference turn.
  const answer = await session.runDecision({
    prompt: fullAuthoritativeContextSnapshot,
    toolNames: ['lookup'],
    model: selectedModel,
    signal,
    onText: async ({ sequence, text }) => {
      // Persist and verify the span before allowing any playback.
      await persistSpan(sequence, text);
    },
  });
  // answer.result is {text, toolCalls}; proposed tools are data only.
} finally {
  await session.close();
}
```

Running `runDecision` makes a subscription inference request. Internet, account access and quota are still required. There is no API-key path or paid fallback, no audio API, and no local model download.

Creation accepts the same Python runtime options as `createAgentSession`: `python`, `uv`, a complete snapshotted `env`, and default `timeoutMs`. `warm` accepts `signal` and `timeoutMs`. `runDecision` accepts exactly `prompt`, `toolNames`, optional `model`, `signal`, `timeoutMs`, and `onText`. A missing model inherits `CODEX_MODEL`, then the model catalog default; `null` explicitly restores the catalog default after an earlier selected model. Each selected model uses its catalog default reasoning effort and standard service tier, matching the ordinary `exec --ignore-user-config` path rather than inheriting a coding-session override. Unlisted model names fail without fallback. The full prompt is preserved. A small transport instruction supplies the record format and states that the current authoritative snapshot supersedes earlier context.

Only one call can be active. Each decision gets a fresh ephemeral thread with the complete authoritative context once; successful completion unsubscribes and waits for that thread to unload. The app-server process stays warm. This prevents full snapshots from accumulating in provider history. Never share a session across application users, conversations or voice calls. Failures, invalid output, interruption and callback rejection stop the provider before another explicit request can start fresh. Integrator never retries a decision automatically or resumes a failed provider thread. Codex's own network transport is still implementation-owned; local process reuse does not establish remote socket reuse.

## Record contract

The provider must output newline-delimited JSON. Exactly one mode is permitted:

```jsonl
{"type":"text","text":"First complete sentence. "}
{"type":"text","text":"Second complete sentence."}
{"type":"done"}
```

or:

```jsonl
{"type":"tools","toolCalls":[{"name":"lookup","argumentsJson":"{}"}]}
{"type":"done"}
```

Text callbacks receive only complete validated records with a zero-based sequence. Each callback is awaited, with at most one record awaiting acknowledgement. Rejecting a callback cancels the request. Cancellation and deadlines still work if a callback never settles. A callback that later settles cannot acknowledge a newer request. Caller callback side effects remain caller-owned: use the same abort signal/generation to prevent late persistence or playback.

Validation requires exact keys, unique JSON keys, finite numbers, known tool names, object-valued JSON arguments, bounded records, a terminal done record, a matching final provider message and successful provider completion. Only positively identified `final_answer` message deltas stream early. Commentary and reasoning are never forwarded. A null phase is buffered until successful completion and reported as `timings.delivery === 'buffered'`. The final message must match accumulated deltas; no final snapshot is replayed through `onText`.

Limits: 8 MiB request, 64 KiB encoded record, 400 UTF-16 code units per text record, 256 text records, 102400 total text code units, 16 tool calls, 32 KiB per encoded tool argument, 1 MiB answer stream, 2 MiB protocol frame, and 32 MiB provider stdout/stderr per operation. At most 256 unique allowed tool names are accepted, each up to 128 characters. The overall Node deadline defaults to 900000 ms; provider execution also honors `CODEX_TIMEOUT_SECONDS` (default 180 seconds), and queueing uses the existing Codex lock/queue timeout.

Each accepted text record is irrevocable. If a later record or provider completion fails, the promise rejects; previously accepted text remains the caller's partial transcript. The caller must stop pending audio and must not replay or erase accepted spans. Tool calls are returned only after complete successful validation and provider completion.

## Isolation and evidence

The bridge retains the existing filtered provider environment and cross-process `_request_slot`, including coordination with ordinary Integrator clients. It uses an empty temporary working directory; read-only, no-network execution sandbox; empty environments, workspace roots, dynamic tools and capability roots; disabled shell/exec, apps, plugins, hooks, agents, browser, computer, image, memories and remote-control features; disabled plan/user-input utilities; no project/skill instructions; and no notify command.

The app-server does not offer `--ignore-user-config`. An initial restricted, no-thread configuration probe discovers inherited MCP names, then a fresh process disables every inherited server explicitly. Empty TOML tables merge and cannot safely clear them. Nonstandard MCP key names fail closed. Effective configuration, raw priority layers for omitted utility-tool and immediate thread-unload settings, managed requirements and ChatGPT account type are checked before use. Custom provider endpoint URLs and incompatible managed policies are refused. Existing login/credential stores remain Codex-owned; no credentials are opened or copied by Integrator. `CODEX_HOME` is not replaced with a synthetic auth copy or debug-only config-isolation variable.

Source inspection of Codex 0.157.1 shows that empty `environments` removes environment-bound tools, including `apply_patch`; disabling shell flags alone does not. Some model catalogs may still advertise non-host utilities. Any unexpected native tool item, server request, approval, model reroute or provider error terminates the request. These provider controls are not a separate operating-system sandbox for the CLI itself.

Run deterministic fixture suites with `npm test`, `npm run test:python`, and `npm run test:pack`. Run the local no-inference configuration/thread smoke with:

```sh
uv run --project python --frozen python scripts/check-provider-isolation.py
```

The smoke creates an ephemeral thread but never calls `turn/start`. It prints booleans and elapsed time, never account/configuration contents. No live model output, speech latency, microphone or speaker behavior has been verified by these tests.

Timings are `startupMs`, `inferenceMs` (queue, provider processing, validation and callback backpressure), `totalMs`, `reusedRuntime`, `reusedProvider`, `firstTextMs` (first validated record from call start, before awaiting the callback), and `delivery`. Usage is the app-server's per-turn `tokenUsage.last` object, or `{}` if no usage event arrived. Unknown usage is not zero usage.

### Internal phase timings

Successful results additionally return `timings.phases`. These values use Python's monotonic clock and exclude runtime startup. They contain numbers or explicit nulls only; no provider content is included.

| Field | Meaning |
| --- | --- |
| `lockWaitMs` | Waiting for the shared cross-process request lock |
| `preparationMs` | Account/config checks, fresh thread creation and request preparation after acquiring the lock |
| `accountReadMs`, `requirementsReadMs`, `configReadMs` | Readiness RPC and local validation durations, already included in `preparationMs` |
| `threadConfigReadMs`, `threadCreateMs` | Config recheck before thread creation, and thread/start plus validation; already included in `preparationMs` |
| `turnStartAckMs` | From turn/start dispatch to its RPC acknowledgement |
| `firstDeltaMs` | From dispatch to observing the first nonempty answer-protocol delta; not validated text and never forwarded by this metric |
| `firstValidatedTextMs` | From dispatch to the first valid text record, before invoking/awaiting its callback |
| `turnCompletedMs` | From dispatch to observing successful provider completion |
| `textCallbackMs` | Accumulated time sending validated text and waiting for the caller's acknowledgement |
| `cleanupMs` | Thread unsubscribe through observed thread/closed |
| `totalMs` | Complete Python decision, including queue, setup, callbacks and cleanup |

These are overlapping milestones and durations, not independent stages to sum. Callback backpressure can delay reading later provider events even if the provider already emitted them. `firstDeltaMs` observes arrival/consumption, not a remote model's internal timestamp; the RPC acknowledgement can also precede consumption of already queued deltas. `firstValidatedTextMs - firstDeltaMs` measures the wait for a complete accepted record, including buffering. Tool-only output has `firstValidatedTextMs: null`; a missing raw delta is null rather than zero. Unknown-phase text is accepted only after successful completion. The metadata cannot separate provider-side network queueing from model compute before the first delta.

The bundled private bridge uses protocol 3 for this exact numeric payload. Public `runAgent`, `createAgentSession`, decision text and callback contracts are unchanged. Failed/cancelled operations still reject without a final phase object; applications should retain their existing partial timing trace. No live inference is required to verify these fields: fixture tests hold the shared lock, delay thread creation/RPC acknowledgement, split text records and delay callbacks independently.

## Latency regression correction (October 18, 2026)

The initial persistent implementation inherited the owner’s `xhigh` reasoning and appended each full application snapshot to the previous provider turn. Owner-call input grew 8,180 → 12,499 → 16,779 tokens. The corrected path retains the provider process but isolates decision contexts and restores catalog model/effort defaults. Four owner-authorized text-only requests on the same gpt-6-astra model measured first text at 9.216/9.907 s with xhigh and 5.050/5.480 s with medium. This small sample does not guarantee latency or prove answer-quality parity; one medium request had cached input, so the uncached greeting pair is the cleaner comparison. No speech API, microphone or speaker was used.
