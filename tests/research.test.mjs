import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, writeFile, readFile, rm, chmod } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { setTimeout as delay } from "node:timers/promises";
import * as api from "../src/index.js";
const python = fileURLToPath(
  new URL("../python/.venv/bin/python", import.meta.url),
);
const schema = {
  type: "object",
  additionalProperties: false,
  required: ["answer", "urls"],
  properties: {
    answer: { type: "string" },
    urls: { type: "array", items: { type: "string" } },
  },
};
async function fixture(t, options = {}) {
  const root = await mkdtemp(join(tmpdir(), "integrator-research-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  const executable = join(root, "codex");
  await writeFile(
    executable,
    (
      await readFile(
        new URL("./fixtures/research-server.py", import.meta.url),
        "utf8",
      )
    ).replace("#!/usr/bin/env python3", `#!${python}`),
  );
  await chmod(executable, 0o700);
  await writeFile(join(root, "options.json"), JSON.stringify(options));
  return {
    root,
    request: {
      prompt: "A public question",
      schema,
      webSearch: true,
      python,
      timeoutMs: 4000,
      env: {
        ...process.env,
        CODEX_BIN: executable,
        CODEX_LOCK_PATH: join(root, "request.lock"),
        APP_SECRET: "secret",
        OPENAI_API_KEY: "secret",
        CODEX_MODEL: "",
      },
    },
    logs: async () =>
      (await readFile(join(root, "requests.jsonl"), "utf8"))
        .trim()
        .split("\n")
        .map(JSON.parse),
  };
}
test("research returns observed search separately from schema-validated answer", async (t) => {
  assert.equal(typeof api.runResearch, "function");
  const f = await fixture(t);
  const answer = await api.runResearch(f.request);
  assert.equal(answer.result.answer, "A bounded result");
  assert.deepEqual(answer.searches, [
    { query: "official example", urls: ["https://example.com/evidence"] },
  ]);
  assert.deepEqual(answer.usage, { inputTokens: 10, outputTokens: 5 });
  const turns = (await f.logs()).filter((x) => x.method === "turn/start");
  assert.equal(turns.length, 1);
  assert.equal(turns[0].params.model, "catalog-default");
  const config = (await f.logs()).find((x) => x.spawn).config;
  assert.deepEqual(config.features.code_mode, {
    enabled: false,
    direct_only_tool_namespaces: ["web"],
  });
  assert.equal(config.features.code_mode_host, false);
});
test("research synthesis is web-disabled and retains unknown usage as null", async (t) => {
  const f = await fixture(t, { noUsage: true });
  const answer = await api.runResearch({
    ...f.request,
    webSearch: false,
    model: "selected-model",
  });
  assert.deepEqual(answer.searches, []);
  assert.equal(answer.usage, null);
  assert.deepEqual((await f.logs()).find((x) => x.spawn).config.features.code_mode, {
    enabled: false,
    direct_only_tool_namespaces: [],
  });
  assert.equal(
    (await f.logs()).find((x) => x.method === "turn/start").params.model,
    "selected-model",
  );
});
for (const configWarning of [
  "Codex is ignoring 1 unrecognized configuration setting. Check for typos or deprecated settings.\n  session-flags: `features.rmcp_client` is ignored.",
  "Codex could not find bubblewrap on PATH. Install bubblewrap with your OS package manager. See the sandbox prerequisites: https://developers.openai.com/codex/concepts/sandboxing#prerequisites. Codex will use the bundled bubblewrap in the meantime.",
]) test("research tolerates a pinned CLI compatibility notice after verifying restrictions: " + configWarning.slice(0, 40), async (t) => {
  const f = await fixture(t, { configWarning });
  const answer = await api.runResearch(f.request);
  assert.equal(answer.result.answer, "A bounded result");
});
test("research accepts remote-control disabled status", async (t) => {
  const f = await fixture(t, { remoteStatus: "disabled" });
  assert.equal((await api.runResearch(f.request)).result.answer, "A bounded result");
});
test("research accepts an unchanged ChatGPT account notification", async (t) => {
  const f = await fixture(t, { updatedAccount: "chatgpt" });
  assert.equal((await api.runResearch(f.request)).result.answer, "A bounded result");
});
test("research accepts moderation metadata for its own turn", async (t) => {
  const f = await fixture(t, { moderationMetadata: true });
  assert.equal((await api.runResearch(f.request)).result.answer, "A bounded result");
});
for (const [name, options, override] of [
  ["non-web direct tools", { unsafeDirect: true }, {}],
  ["code mode enabled", { unsafeCodeMode: true }, {}],
  ["code host enabled", { unsafeCodeHost: true }, {}],
  ["moderation metadata from another thread", { moderationMetadata: true, wrongMetadataIdentity: true }, {}],
  ["account switching to API key", { updatedAccount: "apiKey" }, {}],
  ["remote-control enabled status", { remoteStatus: "enabled" }, {}],
  ["unknown configuration warning", { configWarning: "Unknown security restriction was ignored" }, {}],
  ["no observed search", { noSearch: true }, {}],
  ["host command", { native: "commandExecution" }, {}],
  ["file edit", { native: "fileChange" }, {}],
  ["MCP", { native: "mcpToolCall" }, {}],
  ["approval", { approval: true }, {}],
  ["effective unsafe config", { unsafeConfig: true }, {}],
  ["overridden provider endpoint", { unsafeProvider: true }, {}],
  ["managed search restriction", { denyWeb: true }, {}],
  ["API-key login", { apiKey: true }, {}],
  ["API-key account", { apiKeyAccount: true }, {}],
  ["unqualified version", { version: "0.164.0" }, {}],
  ["unknown model", {}, { model: "unavailable" }],
  ["failed turn", { failed: true }, {}],
  ["mismatched final output", { mismatch: true }, {}],
  ["wrong identity", { wrongIdentity: true }, {}],
  ["duplicate search completion", { duplicateSearch: true }, {}],
  ["invalid schema result", { raw: '{"wrong":true}' }, {}],
  [
    "unexpected synthesis search",
    { unexpectedWeb: true },
    { webSearch: false },
  ],
])
  test(`research rejects ${name} without leaking diagnostics`, async (t) => {
    const f = await fixture(t, options);
    await assert.rejects(
      api.runResearch({ ...f.request, ...override }),
      (error) =>
        error instanceof api.ProviderError &&
        !error.message.includes("do-not-leak"),
    );
  });
test("cancellation terminates the research process group including descendants", async (t) => {
  const f = await fixture(t, { stall: true, child: true });
  const control = new AbortController();
  const pending = api.runResearch({ ...f.request, signal: control.signal });
  let pid;
  for (let i = 0; i < 200 && !pid; i++) {
    try {
      pid = Number(await readFile(join(f.root, "child.pid"), "utf8"));
    } catch {
      await delay(10);
    }
  }
  assert.ok(pid, "provider child started");
  control.abort();
  await assert.rejects(pending, /cancelled/);
  for (let i = 0; i < 100; i++) {
    try {
      process.kill(pid, 0);
      await delay(10);
    } catch {
      return;
    }
  }
  assert.fail("research descendant survived cancellation");
});
test("overall deadline includes waiting for the existing shared request lock", async (t) => {
  const f = await fixture(t, { stall: true });
  const firstControl = new AbortController();
  const first = api
    .runResearch({ ...f.request, signal: firstControl.signal })
    .catch(() => {});
  for (let i = 0; i < 100; i++) {
    try {
      if ((await f.logs()).some((x) => x.method === "turn/start")) break;
    } catch {}
    await delay(10);
  }
  const started = Date.now();
  await assert.rejects(
    api.runResearch({ ...f.request, timeoutMs: 100 }),
    /timed out/,
  );
  assert.ok(Date.now() - started < 1500);
  firstControl.abort();
  await first;
});
test("research input rejects unsupported providers and oversized or externally referenced schemas", async () => {
  for (const override of [
    { provider: "other" },
    { schema: { $ref: "https://example.com/schema" } },
    { webSearch: "live" },
    { prompt: "x".repeat(100000) },
    { timeoutMs: 240001 },
  ])
    await assert.rejects(
      api.runResearch({
        prompt: "question",
        schema,
        webSearch: true,
        ...override,
      }),
      api.ProviderError,
    );
});
