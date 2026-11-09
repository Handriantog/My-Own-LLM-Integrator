// Separate contract: changing this cannot enable Web in ordinary inference.
export function researchRequest(options) {
  const {
    prompt,
    schema,
    webSearch,
    provider,
    model,
    timeoutMs = 240_000,
    ...runtime
  } = options ?? {};
  if (
    typeof prompt !== "string" ||
    !prompt.trim() ||
    Buffer.byteLength(prompt) > 80 * 1024 ||
    typeof webSearch !== "boolean" ||
    (provider !== undefined && provider !== "codex") ||
    !Number.isFinite(timeoutMs) ||
    timeoutMs <= 0 ||
    timeoutMs > 240_000 ||
    !schema ||
    typeof schema !== "object" ||
    Array.isArray(schema) ||
    (model !== undefined &&
      model !== null &&
      (typeof model !== "string" || !model.trim()))
  )
    throw Error();
  const encoded = JSON.stringify(schema);
  if (Buffer.byteLength(encoded) > 16 * 1024) throw Error();
  const plain = JSON.parse(encoded);
  function visit(value, depth = 0) {
    if (depth > 16) throw Error();
    if (value && typeof value === "object")
      for (const [key, item] of Object.entries(value)) {
        if (
          ["$ref", "$dynamicRef", "$recursiveRef"].includes(key) &&
          (typeof item !== "string" || !item.startsWith("#"))
        )
          throw Error();
        visit(item, depth + 1);
      }
  }
  visit(plain);
  return {
    request: {
      prompt,
      schema: plain,
      webSearch,
      timeoutMs,
      ...(model !== undefined ? { model } : {}),
    },
    runtime: { ...runtime, timeoutMs },
  };
}
