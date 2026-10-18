export class ProviderError extends Error {}
export interface Provider { id: string; name: string; executable: string }
export interface RuntimeOptions {
  signal?: AbortSignal;
  /** Python interpreter with jsonschema installed; otherwise uses uv. */
  python?: string;
  uv?: string;
  /** Complete environment for the engine; the provider receives a filtered subset. */
  env?: Record<string, string | undefined>;
  /** Overall deadline, including runtime startup; default 900000. */
  timeoutMs?: number;
}
export interface AgentOptions extends RuntimeOptions {
  prompt: string;
  schema: Record<string, unknown>;
  provider?: string;
  /** Omit to inherit the environment; null selects the CLI default. */
  model?: string | null;
}
export interface AgentResult<T = Record<string, unknown>> {
  result: T;
  usage: Record<string, unknown>;
}
export function runAgent<T = Record<string, unknown>>(options: AgentOptions): Promise<AgentResult<T>>;
export function listProviders(options?: RuntimeOptions): Promise<Provider[]>;
export function validateProvider(provider?: string, options?: RuntimeOptions): Promise<Provider>;

export interface AgentSessionOptions {
  /** Python interpreter with the engine dependencies installed; otherwise uses uv. */
  python?: string;
  uv?: string;
  /** Complete environment, snapshotted at creation and reused on cold restarts. */
  env?: Record<string, string | undefined>;
  /** Default overall deadline for each call, including startup; default 900000. */
  timeoutMs?: number;
}
export interface AgentSessionCallOptions {
  signal?: AbortSignal;
  /** Overall deadline for this call, including runtime startup. */
  timeoutMs?: number;
}
export interface AgentSessionRunOptions extends AgentSessionCallOptions {
  prompt: string;
  schema: Record<string, unknown>;
  provider?: string;
  /** Omit to inherit the environment; null selects the CLI default. */
  model?: string | null;
}
export interface AgentSessionTimings {
  /** Spawn through ready handshake, or zero when the runtime is reused. */
  startupMs: number;
  /** Engine queue, provider execution, and schema validation. */
  inferenceMs: number;
  /** Whole JavaScript call, including startup and protocol transport. */
  totalMs: number;
  reusedRuntime: boolean;
}
export interface AgentSessionResult<T = Record<string, unknown>> extends AgentResult<T> {
  timings: AgentSessionTimings;
}
export interface AgentSession {
  /** Prepare the runtime without invoking a provider. Concurrent calls reject. */
  warm(options?: AgentSessionCallOptions): Promise<{ startupMs: number }>;
  /** Return one validated result. Concurrent calls reject; requests are never retried. */
  runAgent<T = Record<string, unknown>>(options: AgentSessionRunOptions): Promise<AgentSessionResult<T>>;
  /** Stop and reap the runtime. Idempotent; a closed session cannot be reused. */
  close(): Promise<void>;
}
export function createAgentSession(options?: AgentSessionOptions): AgentSession;

/** Independently validated text record. Sequence starts at zero each decision. */
export interface ProviderTextRecord { sequence: number; text: string }
export interface ProviderToolCall { name: string; argumentsJson: string }
export interface ProviderDecisionOptions extends AgentSessionCallOptions {
  /** Complete authoritative conversation/context snapshot for this decision. */
  prompt: string;
  /** Allowed application tool names; tools are returned as data, never executed. */
  toolNames: string[];
  /** Omit to inherit CODEX_MODEL; null selects the session's CLI default. */
  model?: string | null;
  /** Awaited for each complete record. Failure cancels; accepted records are irrevocable. */
  onText?: (record: ProviderTextRecord) => void | Promise<void>;
}
/** Python monotonic measurements for a successful decision, excluding runtime startup.
 * Milestones are observed after turn/start dispatch. They overlap and are not additive.
 */
export interface ProviderPhaseTimings {
  /** Wait for the shared cross-process request lock. */
  lockWaitMs: number;
  /** Auth/config checks, fresh thread and request preparation after lock acquisition. */
  preparationMs: number;
  /** Preparation subdurations; already included in preparationMs. */
  accountReadMs: number;
  requirementsReadMs: number;
  configReadMs: number;
  threadConfigReadMs: number;
  threadCreateMs: number;
  /** turn/start dispatch through RPC acknowledgement. */
  turnStartAckMs: number;
  /** Dispatch through first nonempty answer-protocol delta, before record validation.
   * Never exposes raw text. Null if no delta was observed. */
  firstDeltaMs: number | null;
  /** Dispatch through first valid text record, before caller acknowledgement.
   * Null for tool-only replies. Unknown-phase text waits for successful completion. */
  firstValidatedTextMs: number | null;
  /** Dispatch through observing a successful turn/completed event. */
  turnCompletedMs: number;
  /** Accumulated text callback/acknowledgement wait; overlaps the milestones. */
  textCallbackMs: number;
  /** Unsubscribe through observing thread/closed. */
  cleanupMs: number;
  /** Entire Python run, including lock, preparation, callbacks and cleanup. */
  totalMs: number;
}
export interface ProviderSessionTimings extends AgentSessionTimings {
  /** Reused local app-server process, not a claim about remote connection reuse. */
  reusedProvider: boolean;
  /** Call start through first validated record, before awaiting its callback. */
  firstTextMs: number | null;
  /** Unknown-phase messages are buffered until successful provider completion. */
  delivery: 'incremental' | 'buffered';
  phases: ProviderPhaseTimings;
}
export interface ProviderDecisionResult extends AgentResult<{text: string; toolCalls: ProviderToolCall[]}> {
  timings: ProviderSessionTimings;
}
export interface ProviderSession {
  /** Verify restricted configuration and ChatGPT auth; starts no inference turn. */
  warm(options?: AgentSessionCallOptions): Promise<{startupMs: number}>;
  /** One active decision. No retries; errors discard the provider context. */
  runDecision(options: ProviderDecisionOptions): Promise<ProviderDecisionResult>;
  /** Cancel active work and stop the call-scoped provider. Idempotent. */
  close(): Promise<void>;
}
/** Codex-only additive session; never share across application conversations/calls. */
export function createProviderSession(options?: AgentSessionOptions): ProviderSession;
