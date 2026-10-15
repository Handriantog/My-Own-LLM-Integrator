# MyOwnLlmIntegrator

Use installed, authenticated AI CLIs from local services through one npm API.
Extracted from MyOwnResearcher so other MyOwnStack apps can share the same engine.

This package preserves the Python subprocess engine and adds a JavaScript API,
TypeScript declarations, and a JSON CLI. It does not depend on MyOwnResearcher,
Flask, CAMEL, Graphiti, or an HTTP server.

## Requirements

- macOS, Linux, or WSL; native Windows is unsupported.
- Node.js 22.12+ and uv with Python 3.11+.
- An installed provider CLI, signed in under the same OS account as your app.

The Node API uses `uv run` with the bundled, locked Python project. First use may
download Python/dependencies and creates `python/.venv` inside the installed
package; the installation must be writable. To provision explicitly, run
`uv sync --project node_modules/myown-llm-integrator/python --frozen --no-dev`.
Alternatively pass `python: '/path/to/venv/bin/python'` or set `MYOWN_LLM_PYTHON`
to Python 3.11+ with `jsonschema>=4,<5` installed. This avoids uv and requires no
writes to the installed package at runtime.

Install/login to your provider separately. The library does not install provider
CLIs, create accounts, or copy authentication tokens. Codex requires ChatGPT
login; API-key-only Codex sessions are rejected. Other CLIs use their configured
authentication and account rules. Presets are experimental and version-sensitive;
see [provider details](docs/providers.md).

Services run locally; inference may be remote and consumes your provider account's
allowance. No automatic API fallback is performed.

## Local development and installation

This repository is not published to npm yet. Keep it beside the consuming app:

```text
MyOwnStack/
  MyOwnLlmIntegrator/
  MyOwnResearcher/
```

```sh
cd MyOwnLlmIntegrator
npm ci                         # builds dist/
npm run setup:python
npm run check

cd ../MyOwnResearcher
npm install ../MyOwnLlmIntegrator
```

`npm ci` in this repo builds `dist/` even if a consumer's npm policy blocks
dependency lifecycle scripts. After editing `src/`, run `npm run build` here.
Python changes are immediately available to local editable consumers.

To install a self-contained artifact instead of a sibling link:

```sh
cd MyOwnLlmIntegrator
npm pack
# In any consumer project:
npm install /path/to/myown-llm-integrator-0.1.0.tgz
```

The tarball contains the built JS API/CLI, declarations, Python source/lockfile,
license, and documentation. It excludes virtual environments and local secrets.

## JavaScript API

```js
import { runAgent, listProviders, validateProvider } from 'myown-llm-integrator';

console.log(await listProviders());
await validateProvider('codex'); // configuration/executable check; no inference

const { result, usage } = await runAgent({
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
console.log(result.answer, usage);
```

`runAgent(options)` returns `{ result, usage }`. The result is validated against
your JSON Schema; usage is provider-reported and may be empty. Missing usage is
unknown, not zero. TypeScript consumers can supply a result type parameter.

| Option | Meaning |
| --- | --- |
| `prompt`, `schema` | Required prompt string and JSON Schema object |
| `provider` | Provider ID/alias; omitted uses `LLM_PROVIDER`, then `codex` |
| `model` | Explicit model; omit to use `CODEX_MODEL`/`AGENT_MODEL`, or `null` for CLI default |
| `signal` | AbortSignal; cancellation terminates the inference process |
| `python` | Explicit Python interpreter, bypassing uv |
| `uv` | uv executable path; default `uv` |
| `env` | Complete engine environment; defaults to `process.env` |
| `timeoutMs` | Overall deadline including runtime startup; default 900,000 ms |

For per-call configuration use `env: { ...process.env, AGENT_BIN: '/path/to/cli' }`.
The engine filters the environment before launching the provider. API keys and
application secrets are not implicitly inherited; non-Codex providers can opt in
to named variables through `AGENT_ENV_PASSTHROUGH`.

Failures reject with `ProviderError` containing a sanitized message. The API
returns one final structured result; streaming is unsupported. Tool decisions can
be represented as JSON, but executing application actions is the caller's job.
Provider restrictions are not an OS sandbox; some CLIs can still load user
tools/hooks. See the provider guide before enabling them.

## Configuration and wrappers

Existing `CODEX_*` and `AGENT_*` settings remain supported. Execution defaults to
180 seconds; queue wait defaults to 600 seconds. Requests are serialized across
processes using kernel-owned locks. Defaults are
`$XDG_STATE_HOME/myown-llm-integrator/{codex,agent}.lock`, with `~/.local/state`
used when `XDG_STATE_HOME` is unset. Set absolute `CODEX_LOCK_PATH`/
`AGENT_LOCK_PATH` to share a different pool.

For non-Codex providers, `AGENT_COMMAND_JSON` is an argv array for a custom
wrapper. It reads the prompt/schema on stdin and writes one raw JSON object to
stdout. No shell expansion occurs. `MYOWN_LLM_PROVIDER` and `MYOWN_LLM_MODEL`
identify the request; legacy `MIROFISH_AGENT_PROVIDER` and `MIROFISH_AGENT_MODEL`
remain available. Native presets do not require a wrapper.

## CLI

```sh
npx --no-install myown-llm-integrator providers
npx --no-install myown-llm-integrator check codex
printf '%s' '{"provider":"codex","prompt":"Say hello","schema":{"type":"object"}}' \
  | npx --no-install myown-llm-integrator
```

Inference prints `{result, usage}` as JSON. Failures go to stderr and exit nonzero.
Examples that request inference consume account allowance.

## Python consumers

The engine is installable from the npm package's `python` directory. Install the
npm dependency first, then configure uv/pip to use that path. For example:

```toml
[project]
dependencies = ["myown-llm-integrator==0.1.0"]

[tool.uv.sources]
myown-llm-integrator = { path = "../node_modules/myown-llm-integrator/python", editable = true }
```

```python
from myown_llm_integrator import run_agent

result, usage = run_agent('Say hello', {'type': 'object'}, provider='codex')
```

Python calls accept `model`, a cancellation event, and `serialize=False` for a
caller that already owns cross-process scheduling. MyOwnResearcher uses this
option while retaining run concurrency limits and usage accounting. Its
research-specific Chat Completions/CAMEL/Graphiti adapters remain in that app.

## Validation and provenance

`npm run check` runs build, Node tests, and Python tests using fake CLI processes.
`npm run test:pack` installs and exercises a tarball in a separate temporary app.
Tests do not call real providers. No live provider compatibility was newly
established by this extraction; the experimental presets were preserved.

AGPL-3.0-only, inherited from the source project. See [NOTICE](NOTICE) and [LICENSE](LICENSE).
