// summarize-news.mjs PROMPT_FILE DIGEST_FILE OUT_FILE
// One direct model call: prompt + digest in, JSON out. No agent loop, no tool
// schemas, no skills index — only the tokens the task needs.
// Uses Hermes' configured model (~/.hermes/config.yaml), so switching the Hermes
// model switches this too. NEWS_MODEL=... pins a model for this job only.
// If the primary keeps failing it falls back to Hermes' fallback_model, or to
// Hermes' default model when NEWS_MODEL is set.
// Output is normalised afterwards by `news-daily.js clean`.
import fs from "node:fs";

const [promptFile, digestFile, outFile] = process.argv.slice(2);
const HERMES = `${process.env.HOME}/.hermes`;

const config = fs.readFileSync(`${HERMES}/config.yaml`, "utf8");
const modelBlock = config.match(/^model:\n((?:[ \t]+.*\n?)*)/m)?.[1] ?? "";
const field = (name) => modelBlock.match(new RegExp(`^\\s+${name}:\\s*["']?([^"'\\n#]+)`, "m"))?.[1].trim();
const model = process.env.NEWS_MODEL || field("default");
// Same provider only: the API key below is the primary provider's
const fallbackBlock = config.match(/^fallback_model:\n((?:[ \t]+.*\n?)*)/m)?.[1] ?? "";
const fallback = fallbackBlock.match(/^\s+model:\s*["']?([^"'\n#]+)/m)?.[1].trim() || field("default");
const provider = field("provider") || "openrouter";
const baseUrl = (field("base_url") || "https://openrouter.ai/api/v1").replace(/\/$/, "");

const env = fs.readFileSync(`${HERMES}/.env`, "utf8");
const keyName = `${provider.toUpperCase().replace(/-/g, "_")}_API_KEY`;
const key = env.match(new RegExp(`^${keyName}=(.*)$`, "m"))?.[1].trim().replace(/^["']|["']$/g, "");
if (!model || !key) throw new Error(`model or ${keyName} missing in ~/.hermes config/.env`);

const messages = [
  { role: "system", content: fs.readFileSync(promptFile, "utf8") },
  { role: "user", content: "Digest:\n" + fs.readFileSync(digestFile, "utf8") + "\nReply with the JSON only." },
];

async function ask(model) {
  const res = await fetch(`${baseUrl}/chat/completions`, {
    method: "POST",
    headers: { Authorization: `Bearer ${key}`, "Content-Type": "application/json" },
    body: JSON.stringify({
      model, messages, temperature: 0.2,
      // Extraction task: the digest grows with the feed (346 lines on a busy day),
      // so reasoning + answer together can exceed the old 8k cap and the model
      // returns finish=length with no JSON. Budget both generously.
      reasoning: { max_tokens: 8000 },
      max_tokens: 32000,
    }),
    signal: AbortSignal.timeout(300_000),
  });
  const body = await res.json();
  if (!res.ok || body.error) throw new Error(`HTTP ${res.status}: ${JSON.stringify(body.error ?? body).slice(0, 150)}`);

  const choice = body.choices?.[0];
  const text = choice?.message?.content ?? "";
  if (!text.includes("{")) {
    throw new Error(`no JSON from ${body.model} (finish: ${choice?.finish_reason}, usage: ${JSON.stringify(body.usage?.completion_tokens_details ?? body.usage)})`);
  }
  const json = JSON.parse(text.slice(text.indexOf("{"), text.lastIndexOf("}") + 1));
  if (!Array.isArray(json.stories) || json.stories.length === 0) throw new Error("reply has no stories");
  return { json, used: body.model, usage: body.usage ?? {} };
}

// Retry transient failures (rate limits, refusals, malformed replies): two tries
// on the primary model, then two on the fallback
const attempts = [model, model, ...(fallback && fallback !== model ? [fallback, fallback] : [model])];
for (const [i, m] of attempts.entries()) {
  const attempt = i + 1;
  try {
    const { json, used, usage } = await ask(m);
    fs.writeFileSync(outFile, JSON.stringify(json, null, 2) + "\n");
    console.log(`✅ ${json.stories.length} stories via ${used} (tokens: ${usage.prompt_tokens} in / ${usage.completion_tokens} out)`);
    process.exit(0);
  } catch (error) {
    console.error(`attempt ${attempt} (${m}): ${error.message}`);
    if (attempt < attempts.length) await new Promise((r) => setTimeout(r, 60_000));
  }
}
process.exit(1);
