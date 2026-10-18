<p align="center">
  <img src="https://raw.githubusercontent.com/Handriantog/My-Own-LLM-Integrator/main/docs/images/integrator-icon.svg" alt="My Own LLM Integrator icon" width="88" height="88">
</p>

<h1 align="center">My Own LLM Integrator</h1>

<p align="center">
  <strong>Your AI account. Every local app.</strong><br>
  One npm interface for the agent CLIs you already use and the services you want to build.
</p>

<p align="center">
  <a href="#quickstart">Get started</a> ·
  <a href="#use-it-in-your-app">Use the API</a> ·
  <a href="#choose-your-provider">Choose your AI</a> ·
  <a href="#how-it-works">How it works</a> ·
  <a href="#part-of-myownstack">MyOwnStack</a>
</p>

<p align="center">
  <strong>15 CLI providers</strong> &nbsp; / &nbsp;
  <strong>JavaScript + Python</strong> &nbsp; / &nbsp;
  <strong>Schema-validated output</strong>
</p>

---

You already have an AI account. Your next local tool should be able to use it. **My Own LLM Integrator connects your app to a locally authenticated agent CLI**, handles the subprocess lifecycle, and returns structured output through a small npm API.

Built for **MyOwnStack**, extracted from **[MyOwnResearcher](https://github.com/Handriantog/My-Own-Researcher)**, and reusable in your own tools. The existing Python engine is preserved behind a JavaScript API, TypeScript declarations, and a command-line interface.

![Node and Python applications share the My Own LLM Integrator engine, which connects to authenticated AI CLIs and returns validated JSON](https://raw.githubusercontent.com/Handriantog/My-Own-LLM-Integrator/main/docs/images/integrator-overview.svg)

> [!NOTE]
> **Local integration, provider-hosted inference.** Your CLI manages login and account access. Requests may go to a remote model service and consume your allowance. The package never silently falls back to a paid API.

## What you get

| | What it does |
| --- | --- |
| 🔑 **Bring your AI account** | Use Codex with ChatGPT login, or one of 14 additional CLI presets with its configured authentication. |
| 🧩 **Share one integration** | Call the npm API from Node, use the bundled engine from Python, or pipe JSON through the CLI. |
| 📐 **Ask for structured results** | Supply a prompt and JSON Schema; receive validated output plus whatever usage the provider reports. |
| ⏱️ **Control the lifecycle** | Execution and queue timeouts, cancellation, process cleanup, and cross-process request serialization. |
| 🔒 **Keep credentials with the CLI** | Provider processes receive a filtered environment. Credentials are not copied into the library. |
| 🛠️ **Adapt your own wrapper** | Supply an argv array for a custom command while keeping the same prompt, schema, and result contract. |

## Quickstart

### 1. Gather the essentials

| Requirement | What you need |
| --- | --- |
| Operating system | macOS, Linux, or WSL |
| Node.js | **22.12+** |
| Python | **3.11+**, managed by [uv](https://docs.astral.sh/uv/) or an existing environment |
| AI provider | An installed agent CLI, signed in under the same OS user as your app |

### 2. Install in your app

From your Node project:

```sh
npm install myown-llm-integrator
uv sync --project node_modules/myown-llm-integrator/python --frozen --no-dev
```

The npm package includes built JavaScript and the Python engine. The `uv sync` step prepares its locked runtime dependencies; if you skip it, the first API call prepares them automatically. For an existing Python environment, see [runtime options](#how-it-works).

Install and sign in to your chosen provider separately; for the default Codex path, run `codex login` and choose **Sign in with ChatGPT**. Installing this npm package does not install or authenticate provider CLIs.

### 3. Check your setup

```sh
npx --no-install myown-llm-integrator providers
npx --no-install myown-llm-integrator check codex
```

The two checks list presets and validate local configuration/executable availability. **They do not run inference or verify login, quota, or model access.** You are ready to make a request through the API below.

<details>
<summary><strong>Develop from source or install a portable tarball</strong></summary>

Use this route before the first npm release or when working on the integrator itself. The destination name `MyOwnLlmIntegrator` matches the local dependency used by other MyOwnStack projects:

```sh
mkdir -p MyOwnStack
cd MyOwnStack
git clone https://github.com/Handriantog/My-Own-LLM-Integrator.git MyOwnLlmIntegrator
cd MyOwnLlmIntegrator
npm ci
npm run setup:python

# In a sibling Node project
cd ../your-local-app
npm install ../MyOwnLlmIntegrator
```

`npm ci` builds the JavaScript distribution. `setup:python` prepares the development environment, including the Python test dependencies.

For a portable artifact, build a tarball in the integrator repository and install it from any consumer project:

```sh
# In MyOwnLlmIntegrator
npm pack

# In your app
npm install /absolute/path/myown-llm-integrator-0.2.0.tgz
```

The tarball includes the built JavaScript API/CLI, TypeScript declarations, Python source and lockfile, documentation, and license. The consumer does not need either source repository beside it.

</details>

## Use it in your app

### JavaScript and TypeScript

Save this as an `.mjs` file in your consumer project, or use it in a project with `"type": "module"`:

```js
import { runAgent } from 'myown-llm-integrator';

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

console.log(result.answer);
console.log(usage);
```

This request uses your provider account. `result` is validated against your schema; `usage` is provider-reported and may be empty. **Missing usage is unknown, not zero.** TypeScript callers can use `runAgent<{ answer: string }>(options)` to type the result.

<details>
<summary><strong>API options, checks, and errors</strong></summary>

| Option | Meaning |
| --- | --- |
| `prompt`, `schema` | Required prompt string and JSON Schema object |
| `provider` | Provider ID or alias; defaults to `LLM_PROVIDER`, then `codex` |
| `model` | Explicit model; omit to inherit `CODEX_MODEL`/`AGENT_MODEL`, or use `null` for the CLI default |
| `signal` | AbortSignal; cancellation terminates the inference process |
| `python` | Explicit Python interpreter, bypassing uv |
| `uv` | uv executable path; default `uv` |
| `env` | Complete engine environment; defaults to `process.env` |
| `timeoutMs` | Overall deadline including runtime startup; default **900,000 ms** |

```js
import {
  listProviders,
  validateProvider,
  ProviderError,
} from 'myown-llm-integrator';

const providers = await listProviders();
const selected = await validateProvider('claude-code');
```

`listProviders()` and `validateProvider()` do not invoke a provider for inference. Provider failures reject with a sanitized `ProviderError`; raw CLI logs are not returned. The API returns one final structured result and does not support streaming.

For repeated requests, the additive [`createAgentSession` API](docs/latency-sessions.md) reuses a warm Python runtime and reports startup, engine, and total timings. It preserves the existing provider logic and validation. `runAgent` remains unchanged; adopting sessions is an explicit caller choice.

To override configuration for one call, use `env: { ...process.env, AGENT_BIN: '/absolute/path/to/cli' }`. Model selection does not mutate the caller's environment.

</details>

### Command line

The same engine accepts a JSON request on stdin and prints `{result, usage}`:

```sh
printf '%s' '{"provider":"codex","prompt":"Say hello","schema":{"type":"object"}}' \
  | npx --no-install myown-llm-integrator
```

Failures go to stderr and exit nonzero. As with the API example, this command performs inference and uses account allowance.

<details>
<summary><strong>Python applications can use the engine directly</strong></summary>

Install the npm dependency first. In an existing Python project's `pyproject.toml`, add the dependency and its local source. This path assumes a `backend/` project beside the app's `node_modules/` directory:

```toml
[project]
dependencies = ["myown-llm-integrator==0.2.0"]

[tool.uv.sources]
myown-llm-integrator = { path = "../node_modules/myown-llm-integrator/python", editable = true }
```

Then sync that project's environment and call the engine:

```python
from myown_llm_integrator import run_agent

result, usage = run_agent(
    'Say hello',
    {'type': 'object'},
    provider='codex',
)
```

Python calls also accept an explicit `model`, a cancellation event, and `serialize=False` for callers that already own cross-process scheduling. MyOwnResearcher uses this route to preserve its run concurrency limits and usage accounting.

</details>

## Choose your provider

**Codex is the default.** Claude Code, Gemini CLI, Cursor, and the other presets below use the same request interface. Change `provider` in your API call, or set `LLM_PROVIDER` in the engine's environment.

<details>
<summary><strong>All 15 providers and executable names</strong></summary>

| Provider | ID | Executable |
| --- | --- | --- |
| Codex | `codex` | `codex` |
| Claude Code | `claude-code` | `claude` |
| Antigravity | `antigravity` | `agy` |
| Cursor | `cursor` | `agent` |
| Devin CLI | `devin` | `devin` |
| Factory Droid | `factory-droid` | `droid` |
| Gemini CLI | `gemini` | `gemini` |
| GitHub Copilot CLI | `copilot` | `copilot` |
| Grok Build CLI | `grok` | `grok` |
| Kimi Code | `kimi` | `kimi` — current Node.js CLI |
| OpenCode | `opencode` | `opencode` — v2 |
| Pi | `pi` | `pi` |
| Qwen Code | `qwen` | `qwen` |
| Hermes Agent | `hermes` | `hermes` |
| Muse | `muse` | `muse` — Meta Muse Code |

</details>

The package does not install provider CLIs, create accounts, or grant model access. Codex requires a ChatGPT login and rejects API-key-only sessions. Other providers retain their configured authentication, account limits, and billing behavior.

> [!IMPORTANT]
> Native presets are **experimental and version-sensitive**. Tests use fake executables, so a passing suite does not establish live account compatibility. CLI restrictions are not an OS sandbox: some providers can still load user configuration, tools, hooks, or MCP servers.

See the **[provider guide](docs/providers.md)** for installation links, output formats, version boundaries, and tool-control limitations.

## How it works

| Layer | Responsibility |
| --- | --- |
| **Your app** | Supplies the prompt and schema; owns its workflow and any application actions. |
| **npm interface** | Exposes the JavaScript API, types, and CLI; starts a per-request Python subprocess. |
| **Python engine** | Selects the preset, filters the environment, supervises the CLI, validates output, and returns usage. Python apps can call this layer directly. |
| **Provider CLI** | Owns authentication and model access; sends the request to its configured service. |

The engine has no dependency on MyOwnResearcher, Flask, CAMEL, or Graphiti, and requires no HTTP server. Tool decisions can be returned as JSON; dispatching application actions remains your app's responsibility.

<details>
<summary><strong>Python runtime and deployment options</strong></summary>

By default, Node calls use `uv run` with the bundled, locked Python project. First use may download Python/dependencies and creates `python/.venv` inside the installed package, so that installation must be writable.

Provision a consumer's engine explicitly with:

```sh
uv sync --project node_modules/myown-llm-integrator/python --frozen --no-dev
```

For an existing or read-only deployment, pass `python: '/path/to/venv/bin/python'` or set `MYOWN_LLM_PYTHON` to Python 3.11+ with `jsonschema>=4.18,<5` and `referencing>=0.28.4,<1` installed. This bypasses uv and avoids writing to the installed package at runtime. Use the bundled lockfile when you need the same dependency versions as the default runtime.

</details>

<details>
<summary><strong>Timeouts, locking, and custom wrappers</strong></summary>

Existing `CODEX_*` and `AGENT_*` settings remain supported:

| Setting | Default / purpose |
| --- | --- |
| `CODEX_BIN` / `AGENT_BIN` | Override the provider executable |
| `CODEX_MODEL` / `AGENT_MODEL` | Optional model ID; unset uses the CLI default |
| `CODEX_TIMEOUT_SECONDS` / `AGENT_TIMEOUT_SECONDS` | **180 seconds** per execution |
| `CODEX_QUEUE_TIMEOUT_SECONDS` / `AGENT_QUEUE_TIMEOUT_SECONDS` | **600 seconds** waiting for a request slot |
| `CODEX_LOCK_PATH` / `AGENT_LOCK_PATH` | Optional absolute path for cross-process serialization |
| `AGENT_COMMAND_JSON` | JSON argv array replacing a non-Codex native command |
| `AGENT_ENV_PASSTHROUGH` | Explicit names of environment variables allowed into a non-Codex provider |

Default locks live at `$XDG_STATE_HOME/myown-llm-integrator/{codex,agent}.lock`, using `~/.local/state` when `XDG_STATE_HOME` is unset. Cooperating calls share the appropriate lock; callers with their own scheduler can opt out through the Python API.

A custom wrapper reads the prompt/schema on stdin and writes one raw JSON object to stdout. No shell expansion occurs. `MYOWN_LLM_PROVIDER` and `MYOWN_LLM_MODEL` identify the request; legacy `MIROFISH_AGENT_PROVIDER` and `MIROFISH_AGENT_MODEL` remain available.

The engine filters the environment before launching a provider. Application secrets and API keys are not implicitly inherited; `AGENT_ENV_PASSTHROUGH` is an explicit opt-in for named variables.

</details>

## Security and deployment boundaries

The engine starts commands as argument arrays without a shell, filters the provider environment, and returns sanitized provider errors. It limits captured provider stdout and stderr to **32 MiB combined**, and reads at most **32 MiB** of a Codex response file before rejecting excess output. It terminates the request's process group after completion as well as on cancellation, timeout, or output overflow.

Output validation does not fetch external schema references over HTTP or from local files. Include referenced definitions in your schema, for example with `$defs` and `#/$defs/answer`; unresolved references fail with a sanitized error. Keep schemas application-controlled because complex regular expressions and recursive validation can still consume CPU or memory.

Treat `python`, `uv`, `env`, executable paths, and custom wrapper commands as trusted deployment configuration. Do not copy untrusted request objects directly into `runAgent` options. Enable only the providers your application intends to expose.

Provider CLIs run with the service account's filesystem and network access. Environment filtering and CLI flags do not isolate user configuration, credential stores, hooks, or tools. For untrusted prompts or a shared service, use a dedicated restricted account or container with appropriate filesystem, network, and resource limits. See the [provider-specific restrictions](docs/providers.md#native-commands-and-their-limits), including providers that can still execute tools.

## Part of MyOwnStack

**Local services, shared AI integration.** This package is the reusable inference layer for the stack:

| Project | Its job |
| --- | --- |
| **[MyOwnLlmIntegrator](https://github.com/Handriantog/My-Own-LLM-Integrator)** | Connect local services to authenticated AI CLIs through a reusable package. |
| **[MyOwnResearcher](https://github.com/Handriantog/My-Own-Researcher)** | Turn source documents into graphs, simulated perspectives, and research reports. |

```text
MyOwnStack/
├── MyOwnLlmIntegrator/     # This repository
└── MyOwnResearcher/        # First consumer of the npm package
```

Researcher consumes the engine through `node_modules/myown-llm-integrator/python`, keeping research run settings, concurrency, telemetry, and CAMEL/Graphiti adapters in its own repository. Other apps can adopt the same engine without bringing the research workflow along.

## Develop and verify

From the integrator repository:

```sh
npm ci
npm run setup:python
npm run check       # Build + Node tests + Python tests
npm run test:pack   # Install a tarball in an independent temporary app
```

Tests exercise real subprocesses with fake provider executables, including cancellation, parent-exit cleanup, schema validation, and package portability. They do not consume real account quota.

After changing JavaScript source, run `npm run build`. Python source changes are immediately available to editable local consumers. Building this repository directly also works when a consumer's npm policy blocks dependency lifecycle scripts.

Before a release, check known dependency advisories for both runtimes:

```sh
npm audit
audit_requirements=$(mktemp)
uv export --project python --frozen --all-groups --no-emit-project --output-file "$audit_requirements"
uvx pip-audit --no-deps --disable-pip --strict -r "$audit_requirements"
rm "$audit_requirements"
```

These scans cover the npm dependencies and locked Python runtime/test dependencies. They do not audit separately installed provider CLIs, Node/Python/uv itself, or a caller-supplied Python environment. Passing scans and tests do not guarantee that the package or its providers have no vulnerabilities.

## Attribution and license

Extracted from **[MyOwnResearcher](https://github.com/Handriantog/My-Own-Researcher)**, an independent fork of **[MiroFish](https://github.com/666ghj/MiroFish)**. The existing Python transport and provider presets were retained and adapted for reuse under **[AGPL-3.0](LICENSE)**. See [NOTICE](NOTICE) for provenance.

Provider CLIs and services retain their own licenses and terms. This project is independent and is not endorsed by the providers it connects to.

---

<p align="center"><strong>Build the next tool in your own stack.</strong><br><a href="#quickstart">Connect your first app →</a></p>

An additional opt-in [`createProviderSession`](docs/provider-sessions.md) API keeps a restricted Codex 0.157.1 app-server alive for one application call and emits independently validated text records. It requires caller-owned persistence/playback governance; ordinary inference APIs are unchanged.
