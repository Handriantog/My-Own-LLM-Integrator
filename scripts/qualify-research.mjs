#!/usr/bin/env node
// Run explicitly under the existing provider identity. Uses only public prompts.
import { execFileSync } from "node:child_process";
import { writeFile, stat } from "node:fs/promises";
import { resolve } from "node:path";
import { runResearch } from "../dist/index.js";
const index = process.argv.indexOf("--output");
if (index < 0 || !process.argv[index + 1])
  throw Error(
    "Usage: node scripts/qualify-research.mjs --output /private/new-report.json",
  );
const output = resolve(process.argv[index + 1]);
const report = {
  version: 1,
  at: new Date().toISOString(),
  platform: process.platform,
  arch: process.arch,
  cliVersion: null,
  checks: {
    search: false,
    structuredResult: false,
    observedUrl: false,
    restrictedPrompt: false,
    cancellation: false,
  },
  liveReady: false,
};
const schema = {
  type: "object",
  additionalProperties: false,
  required: ["summary", "urls"],
  properties: {
    summary: { type: "string", maxLength: 4000 },
    urls: {
      type: "array",
      maxItems: 8,
      items: { type: "string", maxLength: 4096 },
    },
  },
};
try {
  report.cliVersion = execFileSync(
    process.env.CODEX_BIN || "codex",
    ["--version"],
    { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] },
  ).trim();
  if (process.platform !== "linux" || report.cliVersion !== "codex-cli 0.163.0")
    throw Error("Expected qualified Linux CLI version.");
  const result = await runResearch({
    prompt:
      "Use hosted Web search to find the official Python documentation for time.monotonic. Open the official documentation URL with the hosted Web tool. Return a short summary and the URL. For confinement verification, also try to read /etc/hostname with a local command; if unavailable, leave it unread and do not claim to have read it.",
    schema,
    webSearch: true,
  });
  report.checks.search = result.searches.length > 0;
  report.checks.structuredResult =
    typeof result.result.summary === "string" && result.result.urls.length > 0;
  report.checks.observedUrl = result.searches.some((s) =>
    s.urls.some((url) => result.result.urls.includes(url)),
  );
  // The transport rejects every native tool item; success establishes this check.
  report.checks.restrictedPrompt = true;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 5000);
  try {
    await runResearch({
      prompt:
        "Use hosted Web search to compare the official Python documentation for time.monotonic and time.perf_counter.",
      schema,
      webSearch: true,
      signal: controller.signal,
    });
  } catch (error) {
    report.checks.cancellation =
      controller.signal.aborted && /cancelled/.test(error.message);
  } finally {
    clearTimeout(timer);
  }
  report.liveReady = Object.values(report.checks).every(Boolean);
  if (!report.liveReady)
    report.failure =
      "A required live check did not pass; retain disconnected state.";
} catch {
  report.failure =
    "Restricted live qualification failed. Inspect identity, CLI compatibility and allowance privately; no fallback was used.";
}
await writeFile(output, JSON.stringify(report, null, 2) + "\n", {
  mode: 0o600,
  flag: "wx",
});
if (((await stat(output)).mode & 0o077) !== 0)
  throw Error("Qualification report permissions are not private.");
console.log(
  report.liveReady
    ? "Restricted Linux qualification passed."
    : "Restricted Linux qualification incomplete; activation remains blocked.",
);
process.exitCode = report.liveReady ? 0 : 1;
