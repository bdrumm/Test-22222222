// node --test: the relay against a fake GitHub, no Cloudflare runtime needed.
import { test } from "node:test";
import assert from "node:assert/strict";
import { handleRequest, tripPath, tracePath, validateTrip, keyMatches } from "../src/index.js";

const NOW = Date.UTC(2026, 9, 8, 22, 0, 0); // 2026-10-08 18:00 New York
const env = { GITHUB_TOKEN: "ghp_test", GITHUB_REPO: "bdrumm/whichway-data", APP_KEY: "k-secret", MAX_TRIP_BYTES: "4096", MAX_TRACE_BYTES: "8192" };

/** A GitHub that remembers what was written. */
function fakeGitHub(initial = {}) {
  const files = { ...initial };
  const calls = [];
  const fetch = async (url, init = {}) => {
    const path = decodeURIComponent(url.split("/contents/")[1]);
    calls.push({ method: init.method || "GET", path });
    if (!init.method || init.method === "GET") {
      if (!(path in files)) return new Response("{}", { status: 404 });
      return new Response(JSON.stringify({ sha: "sha-" + path, content: files[path] + "\n" }), { status: 200 });
    }
    const body = JSON.parse(init.body);
    const existed = path in files;
    if (existed && body.sha !== "sha-" + path) return new Response(JSON.stringify({ message: "sha mismatch" }), { status: 409 });
    files[path] = body.content;
    return new Response("{}", { status: existed ? 200 : 201 });
  };
  return { files, calls, fetch };
}

function trip(over = {}) {
  return { id: "CF455EDA-EB49-4364-8E8B-04E616E2FEF4", createdTs: 1791495854, routeLabel: "A/C/E → F at W 4 St-Wash Sq",
           legs: [{ from: "A31S", line: "A_S", to: "A32S" }, { from: "D20S", line: "F_S", to: "F23S" }], events: [{ kind: "departed", ts: 1791496120 }], ...over };
}

function post(path, body, headers = {}) {
  const text = typeof body === "string" ? body : JSON.stringify(body);
  return new Request("https://relay.test" + path, { method: "POST", body: text,
    headers: { "content-type": "application/json", "content-length": String(text.length), "x-whichway-key": "k-secret", "x-whichway-install": "45359D5A-0878-4C73-B233-8DE8EE1DE67B", ...headers } });
}

test("paths follow the app's own layout, by the New York date", () => {
  assert.equal(tripPath(1791495854.0029, "CF455EDA"), "trips/2026/10/08/1791495854-CF455EDA.json");
  assert.equal(tripPath(1791475200, "X"), "trips/2026/10/08/1791475200-X.json"); // 16:00 UTC = 12:00 New York
  assert.equal(tripPath(1791432000, "X"), "trips/2026/10/08/1791432000-X.json"); // 04:00 UTC = 00:00 New York, Oct 8
  assert.equal(tripPath(1791431999, "X"), "trips/2026/10/07/1791431999-X.json"); // a second before: still Oct 7 in New York
  assert.equal(tracePath(1791495853.97), "traces/trace-1791495853.json");
});

test("a trip is written once and the same trip again is left alone", async () => {
  const gh = fakeGitHub();
  const deps = { fetch: gh.fetch, now: () => NOW };
  let r = await handleRequest(post("/v1/trips", trip()), env, deps);
  assert.equal(r.status, 201);
  assert.deepEqual(await r.json(), { ok: true, status: "created", path: "trips/2026/10/08/1791495854-CF455EDA-EB49-4364-8E8B-04E616E2FEF4.json" });
  assert.deepEqual(gh.calls.map((c) => c.method), ["GET", "PUT"]);
  r = await handleRequest(post("/v1/trips", trip()), env, deps);
  assert.equal(r.status, 200);
  assert.equal((await r.json()).status, "unchanged");
  assert.deepEqual(gh.calls.map((c) => c.method), ["GET", "PUT", "GET"], "no second write");
  // a richer record of the same trip replaces it
  r = await handleRequest(post("/v1/trips", trip({ endedBy: "alighted" })), env, deps);
  assert.equal(r.status, 200);
  assert.equal((await r.json()).status, "updated");
  assert.equal(Object.keys(gh.files).length, 1);
  assert.match(Buffer.from(gh.files[Object.keys(gh.files)[0]], "base64").toString(), /alighted/);
});

test("a trace goes under traces/ by its start", async () => {
  const gh = fakeGitHub();
  const r = await handleRequest(post("/v1/traces", { startTs: 1791495853.97, origin: "A31", dest: "F23", columns: ["ts", "stepEnergy", "pushG", "shakeG"], seconds: [[1791495854, 0, 0.1, 0.03]] }), env, { fetch: gh.fetch, now: () => NOW });
  assert.equal(r.status, 201);
  assert.equal((await r.json()).path, "traces/trace-1791495853.json");
});

test("the wrong key, a bad body, a missing install id and an oversize body are refused before GitHub is touched", async () => {
  const gh = fakeGitHub();
  const deps = { fetch: gh.fetch, now: () => NOW };
  assert.equal((await handleRequest(post("/v1/trips", trip(), { "x-whichway-key": "nope" }), env, deps)).status, 401);
  assert.equal((await handleRequest(post("/v1/trips", trip(), { "x-whichway-install": "" }), env, deps)).status, 400);
  assert.equal((await handleRequest(post("/v1/trips", "{not json"), env, deps)).status, 400);
  assert.equal((await handleRequest(post("/v1/trips", trip({ legs: [] })), env, deps)).status, 400);
  assert.equal((await handleRequest(post("/v1/trips", trip({ createdTs: 1700000000 })), env, deps)).status, 400, "a trip from years ago");
  assert.equal((await handleRequest(post("/v1/trips", trip({ createdTs: NOW / 1000 + 7200 })), env, deps)).status, 400, "a trip from the future");
  assert.equal((await handleRequest(post("/v1/trips", trip({ pad: "x".repeat(5000) })), env, deps)).status, 413);
  assert.equal((await handleRequest(post("/v1/other", trip()), env, deps)).status, 404);
  assert.equal((await handleRequest(new Request("https://relay.test/v1/trips"), env, deps)).status, 405);
  assert.deepEqual(gh.calls, []);
  assert.equal(validateTrip(trip({ events: "no" }), NOW / 1000, 120), "events malformed");
});

test("without its secrets the relay says so, and health tells", async () => {
  const bare = { GITHUB_REPO: "bdrumm/whichway-data" };
  assert.equal((await handleRequest(post("/v1/trips", trip()), bare, { fetch: fakeGitHub().fetch, now: () => NOW })).status, 503);
  const h = await handleRequest(new Request("https://relay.test/v1/health"), env, { now: () => NOW });
  assert.deepEqual(await h.json(), { ok: true, repo: "bdrumm/whichway-data", configured: true });
  assert.equal(await keyMatches("k-secret", "k-secret"), true);
  assert.equal(await keyMatches("k-secre", "k-secret"), false);
  assert.equal(await keyMatches(null, "k-secret"), false);
});

test("a phone sending too fast is told to wait, when the plan has rate limits", async () => {
  let n = 0;
  const limiter = { limit: async () => ({ success: ++n <= 2 }) };
  const gh = fakeGitHub();
  const deps = { fetch: gh.fetch, now: () => NOW };
  const e = { ...env, RATE_INSTALL: limiter };
  assert.equal((await handleRequest(post("/v1/trips", trip({ id: "A1B2C3D4-1" })), e, deps)).status, 201);
  assert.equal((await handleRequest(post("/v1/trips", trip({ id: "A1B2C3D4-2" })), e, deps)).status, 201);
  assert.equal((await handleRequest(post("/v1/trips", trip({ id: "A1B2C3D4-3" })), e, deps)).status, 429);
});

test("GitHub refusing the write is reported, not hidden", async () => {
  const fetch = async (url, init = {}) => (init.method === "PUT" ? new Response(JSON.stringify({ message: "Bad credentials" }), { status: 401 }) : new Response("{}", { status: 404 }));
  const r = await handleRequest(post("/v1/trips", trip()), env, { fetch, now: () => NOW });
  assert.equal(r.status, 502);
  assert.match((await r.json()).error, /GitHub write 401: Bad credentials/);
});
