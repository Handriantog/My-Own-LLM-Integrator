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
