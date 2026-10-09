// The trip relay. Any phone running the app POSTs a finished trip (or a motion trace) here with the shared app
// key; the Worker checks it, decides where it goes, and writes it to the private data repository with the
// GitHub token that only the Worker holds. The repository layout is the one the app wrote directly before
// (trips/<y>/<m>/<d>/<start>-<id>.json, traces/trace-<start>.json), so nothing downstream changes.
//
//   POST /v1/trips    body: one trip observation (JSON, as the app records it)
//   POST /v1/traces   body: one motion trace (JSON)
//   GET  /v1/health
//
// Headers: X-WhichWay-Key (the app key), X-WhichWay-Install (the phone's random install id).
// Replies: 201 created, 200 updated or unchanged (the same file again is fine), 400 bad body, 401 wrong key,
// 413 too big, 429 too many, 502 GitHub said no.

const GITHUB_API = "https://api.github.com";

export function jsonResponse(status, body) {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json; charset=utf-8" } });
}

/** Where a trip goes: trips/<year>/<month>/<day>/<start>-<id>.json by the New York date, as the app wrote it. */
export function tripPath(createdTs, id) {
  const d = new Date(createdTs * 1000);
  const parts = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(d);
  const get = (t) => parts.find((p) => p.type === t).value;
  return `trips/${get("year")}/${get("month")}/${get("day")}/${Math.trunc(createdTs)}-${id}.json`;
}

export function tracePath(startTs) {
  return `traces/trace-${Math.trunc(startTs)}.json`;
}

const ID_RE = /^[A-Za-z0-9-]{8,64}$/;
const INSTALL_RE = /^[A-Za-z0-9-]{8,64}$/;

/** The checks a trip must pass: the fields the review reads, sane sizes, a start within the window. */
export function validateTrip(o, nowSec, maxAgeDays) {
  if (!o || typeof o !== "object" || Array.isArray(o)) return "not a JSON object";
  if (typeof o.id !== "string" || !ID_RE.test(o.id)) return "id missing";
  if (typeof o.createdTs !== "number" || !Number.isFinite(o.createdTs)) return "createdTs missing";
  if (o.createdTs < nowSec - maxAgeDays * 86400 || o.createdTs > nowSec + 3600) return "createdTs out of range";
  if (typeof o.routeLabel !== "string" || o.routeLabel.length > 200) return "routeLabel missing";
  if (!Array.isArray(o.legs) || o.legs.length < 1 || o.legs.length > 4) return "legs missing";
  for (const l of o.legs) {
    if (!l || typeof l.line !== "string" || typeof l.from !== "string" || typeof l.to !== "string") return "leg malformed";
  }
  if (o.events !== undefined && (!Array.isArray(o.events) || o.events.length > 200)) return "events malformed";
  return null;
}

export function validateTrace(t, nowSec, maxAgeDays) {
  if (!t || typeof t !== "object" || Array.isArray(t)) return "not a JSON object";
  if (typeof t.startTs !== "number" || !Number.isFinite(t.startTs)) return "startTs missing";
  if (t.startTs < nowSec - maxAgeDays * 86400 || t.startTs > nowSec + 3600) return "startTs out of range";
  if (!Array.isArray(t.seconds) || t.seconds.length > 4 * 3600) return "seconds malformed";
  if (!Array.isArray(t.columns) || t.columns.length < 2 || t.columns.length > 16) return "columns malformed";
  return null;
}

async function sha256(text) {
  return new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text)));
}

/** The app key against the Worker's, in constant time (digests of equal length). */
export async function keyMatches(given, expected) {
  if (typeof given !== "string" || typeof expected !== "string" || !expected) return false;
  const [a, b] = await Promise.all([sha256(given), sha256(expected)]);
  if (typeof crypto.subtle.timingSafeEqual === "function") return crypto.subtle.timingSafeEqual(a, b);
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a[i] ^ b[i];
  return diff === 0;
}

function base64(bytes) {
  let s = "";
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  return btoa(s);
}

/** Creates or replaces one file in the repository; the same content already there is left alone. */
export async function writeFile(env, path, bytes, message, fetchImpl) {
  const headers = {
    authorization: `Bearer ${env.GITHUB_TOKEN}`,
    accept: "application/vnd.github+json",
    "x-github-api-version": "2022-11-28",
    "user-agent": "whichway-trips-relay",
  };
  const url = `${GITHUB_API}/repos/${env.GITHUB_REPO}/contents/${path}`;
  const content = base64(bytes);
  const existing = await fetchImpl(url, { headers });
  let sha;
  if (existing.status === 200) {
    const j = await existing.json();
    if (typeof j.content === "string" && j.content.replace(/\n/g, "") === content) return { status: "unchanged", path };
    sha = j.sha;
  } else if (existing.status !== 404) {
    throw new Error(`GitHub read ${existing.status}`);
  }
  const body = { message, content };
  if (sha) body.sha = sha;
  const put = await fetchImpl(url, { method: "PUT", headers: { ...headers, "content-type": "application/json" }, body: JSON.stringify(body) });
  if (put.status === 201) return { status: "created", path };
  if (put.status === 200) return { status: "updated", path };
  let detail = "";
  try { detail = (await put.json()).message || ""; } catch {}
  throw new Error(`GitHub write ${put.status}${detail ? ": " + detail : ""}`);
}

async function limited(binding, key) {
  if (!binding || typeof binding.limit !== "function") return false;
  try {
    const { success } = await binding.limit({ key });
    return !success;
  } catch {
    return false;
  }
}

export async function handleRequest(request, env, deps = {}) {
  const fetchImpl = deps.fetch || globalThis.fetch.bind(globalThis);
  const nowSec = (deps.now || Date.now)() / 1000;
  const url = new URL(request.url);
  if (request.method === "GET" && url.pathname === "/v1/health") {
    const out = { ok: true, repo: env.GITHUB_REPO, configured: Boolean(env.GITHUB_TOKEN && env.APP_KEY) };
    // ?probe=1: can the token see the repository and write to it? (GitHub answers 404 for a private repository the
    // token was not made for, so the status code alone says what is wrong; nothing secret is returned)
    if (url.searchParams.get("probe") === "1" && env.GITHUB_TOKEN) {
      try {
        const r = await fetchImpl(`${GITHUB_API}/repos/${env.GITHUB_REPO}`, { headers: { authorization: `Bearer ${env.GITHUB_TOKEN}`, accept: "application/vnd.github+json", "user-agent": "whichway-trips-relay" } });
        out.repoStatus = r.status;
        if (r.status === 200) {
          const j = await r.json();
          out.repoVisible = true;
          out.canPush = Boolean(j.permissions && j.permissions.push);
          out.tokenScopes = r.headers.get("x-oauth-scopes") || null;
          out.permissionsHeader = r.headers.get("x-accepted-github-permissions") || null;
        } else {
          out.repoVisible = false;
        }
      } catch (e) {
        out.repoStatus = String(e.message || e);
      }
    }
    return jsonResponse(200, out);
  }
  const kind = url.pathname === "/v1/trips" ? "trip" : url.pathname === "/v1/traces" ? "trace" : null;
  if (!kind) return jsonResponse(404, { ok: false, error: "no such route" });
  if (request.method !== "POST") return jsonResponse(405, { ok: false, error: "POST" });
  if (!env.GITHUB_TOKEN || !env.APP_KEY) return jsonResponse(503, { ok: false, error: "relay not configured" });
  if (!(await keyMatches(request.headers.get("x-whichway-key"), env.APP_KEY))) return jsonResponse(401, { ok: false, error: "key" });
  const install = request.headers.get("x-whichway-install") || "";
  if (!INSTALL_RE.test(install)) return jsonResponse(400, { ok: false, error: "install id" });
  const ip = request.headers.get("cf-connecting-ip") || "unknown";
  if ((await limited(env.RATE_INSTALL, install)) || (await limited(env.RATE_IP, ip))) return jsonResponse(429, { ok: false, error: "too many" });
  const max = Number(kind === "trip" ? env.MAX_TRIP_BYTES || 262144 : env.MAX_TRACE_BYTES || 4194304);
  const declared = Number(request.headers.get("content-length") || 0);
  if (declared > max) return jsonResponse(413, { ok: false, error: "too big" });
  const bytes = new Uint8Array(await request.arrayBuffer());
  if (bytes.length > max) return jsonResponse(413, { ok: false, error: "too big" });
  let doc;
  try {
    doc = JSON.parse(new TextDecoder().decode(bytes));
  } catch {
    return jsonResponse(400, { ok: false, error: "not JSON" });
  }
  const maxAgeDays = Number(env.MAX_AGE_DAYS || 120);
  const problem = kind === "trip" ? validateTrip(doc, nowSec, maxAgeDays) : validateTrace(doc, nowSec, maxAgeDays);
  if (problem) return jsonResponse(400, { ok: false, error: problem });
  const path = kind === "trip" ? tripPath(doc.createdTs, doc.id) : tracePath(doc.startTs);
  const message = kind === "trip" ? `trip: ${String(doc.routeLabel).slice(0, 80)}` : "motion trace";
  try {
    const r = await writeFile(env, path, bytes, message, fetchImpl);
    return jsonResponse(r.status === "created" ? 201 : 200, { ok: true, ...r });
  } catch (e) {
    return jsonResponse(502, { ok: false, error: String(e.message || e) });
  }
}

export default {
  async fetch(request, env) {
    return handleRequest(request, env);
  },
};
