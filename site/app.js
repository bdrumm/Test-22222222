import { barChart, lineChart, heatmap, sparkline, stringline, trackDiagram, fmt, seriesColor } from "./charts.js";
import { createClientLive, lineBoard, planJourneys, journeyFeeds, trainProgress, stationIndex, enumeratePaths, reachableStations, schedHeadwayAt, pathTrips, predictBoard } from "./rt-client.js";

const app = document.getElementById("app");
// Where the JSON lives. Normally next to the page (built site). When GitHub Pages serves the
// repository's source branch instead of the built site, fall back to the published gh-pages
// branch through raw.githubusercontent.com. `?data=<base url>` overrides both (local dev).
let DATA = "data/";
let dataSource = "same-origin";
async function resolveDataBase() {
  const override = new URLSearchParams(location.search).get("data");
  if (override) { DATA = override.endsWith("/") ? override : override + "/"; dataSource = "override"; return; }
  try { const r = await fetch("data/index.json", { cache: "no-cache", method: "HEAD" }); if (r.ok) return; } catch {}
  const m = location.hostname.match(/^([^.]+)\.github\.io$/i);
  const repo = location.pathname.split("/").filter(Boolean)[0];
  if (m && repo) { DATA = `https://raw.githubusercontent.com/${m[1]}/${repo}/gh-pages/data/`; dataSource = "gh-pages (raw)"; }
}
const dataReady = resolveDataBase();
const cache = new Map();
async function load(path) {
  await dataReady;
  if (!cache.has(path)) cache.set(path, fetch(DATA + path, { cache: "no-cache" }).then(r => { if (!r.ok) throw new Error(`${path}: ${r.status}`); return r.json(); }));
  return cache.get(path);
}
const h = (tag, cls, text, parent) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; if (parent) parent.appendChild(e); return e; };
const link = (href, text, parent, cls) => { const a = h("a", cls, text, parent); a.href = href; return a; };
const ROUTE_COLORS = { "1": "#ee352e", "2": "#ee352e", "3": "#ee352e", "4": "#00933c", "5": "#00933c", "6": "#00933c", "7": "#b933ad",
  A: "#0039a6", C: "#0039a6", E: "#0039a6", B: "#ff6319", D: "#ff6319", F: "#ff6319", M: "#ff6319", G: "#6cbe45", J: "#996633", Z: "#996633",
  L: "#a7a9ac", N: "#fccc0a", Q: "#fccc0a", R: "#fccc0a", W: "#fccc0a", S: "#808183", GS: "#808183", SI: "#0039a6" };
function routeBullet(r, parent) { const s = h("span", "route", r, parent); s.style.background = ROUTE_COLORS[r] || "#6b6b6b"; if (["N", "Q", "R", "W"].includes(r)) s.style.color = "#111"; return s; }
const sevClass = s => `s-${(s || "na").toLowerCase()}`;
function badge(label, cls, parent) { const b = h("span", `badge ${cls}`, null, parent); h("span", "dot", null, b); b.appendChild(document.createTextNode(label)); return b; }
let liveTimer = null, clientLive = null;
const hoursText = hs => hs && hs.length ? hs.map(x => `${String(x).padStart(2, "0")}:00`).join(", ") : "all hours";
const causeName = c => (c || "").replace(/_/g, " ");
const dateTime = iso => { try { return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }); } catch { return iso; } };
const pctChange = v => v == null ? "–" : `${v > 0 ? "+" : ""}${(v * 100).toFixed(0)}%`;

// ---------------------------------------------------------------- routing
const routes = { "": home, lines: lines, alerts: alerts, data: dataPage, station: station, live: live, plan: plan, routes: routesPage, model: modelPage, disruptions: disruptionsPage, line: linePage, travel: travelPage };
async function render() {
  const [section = "", ...rest] = location.hash.replace(/^#\/?/, "").split("/");
  const arg = rest.length ? rest.join("/") : undefined;
  document.querySelectorAll("[data-nav]").forEach(a => a.classList.toggle("active", a.dataset.nav === (section || "home")));
  app.replaceChildren(); h("p", "muted", "Loading…", app);
  try {
    const idx = await load("index.json");
    document.getElementById("generated").textContent = `updated ${dateTime(idx.generated_at)}${idx.mode === "synthetic" ? " · synthetic preview" : ""}${dataSource !== "same-origin" ? ` · data: ${dataSource}` : ""}`;
    app.replaceChildren();
    await (routes[section] || home)(idx, arg);
    window.scrollTo(0, 0);
  } catch (err) {
    app.replaceChildren(); const e = h("div", "empty", null, app); h("div", null, "Could not load the site data.", e); h("div", "small muted", String(err.message || err), e);
  }
}
window.addEventListener("hashchange", () => { if (liveTimer) { clearInterval(liveTimer); liveTimer = null; } if (clientLive) { clientLive.stop(); clientLive = null; } render(); });
document.getElementById("theme-toggle").addEventListener("click", () => {
  const root = document.documentElement, cur = root.dataset.theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  root.dataset.theme = cur === "dark" ? "light" : "dark"; try { localStorage.setItem("theme", root.dataset.theme); } catch {} render();
});
try { const t = localStorage.getItem("theme"); if (t) document.documentElement.dataset.theme = t; } catch {}
render();

// ---------------------------------------------------------------- home
async function home(idx) {
  h("h1", null, "Station arrival diagnosis", app);
  h("p", "secondary", "For each monitored platform: what changed versus the baseline, when it happens, where the delay originates, why, how many riders it costs, and how to avoid it.", app);
  const tiles = h("div", "tiles", null, app);
  tile(tiles, "Monitored platforms", idx.targets.length);
  tile(tiles, "Observed arrivals", fmt.compact(idx.status.arrivals_total));
  tile(tiles, "Days with data", idx.status.days_with_data);
  tile(tiles, "Alerts active now", idx.alerts_active);
  const lm = idx.learned_model || {};
  const t2 = h("div", "tiles", null, app); t2.style.marginTop = ".6rem";
  const tm = tile(t2, "Arrival model", lm.status === "ok" ? `${(lm.mae_model || 0).toFixed(0)} s MAE` : "training", lm.status === "ok" ? `vs schedule ${(lm.mae_schedule || 0).toFixed(0)} s${lm.mae_feed != null ? ` · feed ${lm.mae_feed.toFixed(0)} s` : ""} · ${fmt.compact(lm.n_train || 0)} rows` : `${fmt.compact(lm.n_rows || 0)} rows so far`);
  link("#/model", "Model card →", tm, "small");
  const cl = idx.climatology || {};
  const tc = tile(t2, "Disruptions (archive)", cl.n_events ? `${(cl.n_events / cl.weeks).toFixed(0)} / week` : "–", cl.top_routes && cl.top_routes.length ? `most: ${cl.top_routes.slice(0, 3).map(([r, v]) => `${r} ${v}`).join(", ")}` : "alert archive not pulled yet");
  link("#/disruptions", "Climatology →", tc, "small");
  const tl = tile(t2, "Line views", String((idx.lines_view || []).length), "routes with a live Marey chart");
  link("#/line", "Line view →", tl, "small");
  const tt = tile(t2, "ETA samples", fmt.compact(idx.eta_trust_n || 0), "feed predictions benchmarked");
  link("#/model", "Trust by horizon →", tt, "small");
  const grid = h("div", "grid", null, app); grid.style.marginTop = "1rem";
  for (const t of idx.targets) {
    const card = h("div", "card", null, grid);
    const head = h("div", "row between", null, card);
    const title = h("div", null, null, head); t.routes.forEach(r => routeBullet(r, title)); h("strong", null, ` ${t.station_name || t.station}`, title);
    h("div", "small muted", `${t.direction === "N" ? "Uptown / northbound" : "Downtown / southbound"} · platform ${t.stop_id || ""}`, card);
    if (t.status !== "ok") {
      badge("collecting", "s-na", head);
      h("p", "small secondary", t.message || "Not enough data yet.", card);
      link(`#/station/${t.id}`, "Details", card, "small");
      continue;
    }
    const collecting = t.coverage_windows?.mode === "window_only";
    if (collecting) {
      badge("collecting", "s-na", head);
      h("p", "small", "No baseline yet: about two days of arrivals are needed before the window can be compared. The report shows what has been observed so far.", card).style.marginTop = ".6rem";
    } else {
      badge(`${t.severity.label} · ${t.severity.score}`, sevClass(t.severity.label), head);
      const meter = h("div", "meter", null, card); h("span", null, null, meter).style.width = `${Math.min(100, t.severity.score)}%`;
      h("p", "small", t.verdict, card).style.marginTop = ".6rem";
    }
    const kv = h("div", "small secondary", null, card);
    kv.append("Focus hours: "); const hs = h("span", "hours", null, kv); (t.focus_hours || []).forEach(x => h("span", null, String(x).padStart(2, "0"), hs)); if (!t.focus_hours?.length) kv.append("all hours");
    if (t.top_location) h("div", "small secondary", `Where: ${causeName(t.top_location.cause)}`, card);
    if (t.top_cause) h("div", "small secondary", `Why: ${causeName(t.top_cause.cause)} (support ${t.top_cause.score.toFixed(2)})`, card);
    if (t.impact && t.impact.ridership_source !== "no_baseline") h("div", "small secondary", `Impact: ${fmt.num(t.impact.passenger_hours_per_day)} passenger-hours/day`, card);
    h("div", "tiny muted", `${fmt.num(t.arrival_count)} arrivals · ${t.coverage_windows?.span_hours ?? "?"} h of coverage`, card);
    link(`#/station/${t.id}`, "Open report →", card, "small").style.display = "inline-block";
  }
  if ((idx.routes || []).length) {
    h("h2", null, "Routes and transfers", app);
    const rg = h("div", "grid", null, app);
    for (const r of idx.routes) {
      const card = h("div", "card", null, rg);
      const head = h("div", "row between", null, card);
      h("strong", null, r.label, head);
      badge(r.status === "ok" ? "analysed" : "collecting", r.status === "ok" ? "s-low" : "s-na", head);
      if (r.dominant) h("div", "small secondary", `Largest component: ${r.dominant}`, card);
      if (r.top) h("p", "small", r.top, card).style.marginTop = ".5rem";
      else if (r.status === "ok") h("p", "small secondary", "No material cross-line effect found so far.", card).style.marginTop = ".5rem";
      const row = h("div", "row", null, card); row.style.gap = "1rem";
      link(`#/routes/${r.id}`, "Route analysis →", row, "small"); link(`#/plan/${r.id}`, "Plan this trip →", row, "small");
    }
  }
  try {
    const dg = await load("digest.json");
    if (dg && (dg.items || []).length) {
      h("h2", null, "This week in one paragraph", app);
      const card = h("div", "card", null, app); const ul = h("ul", "findings", null, card);
      dg.items.slice(0, 6).forEach(it => { const li = h("li", null, null, ul); h("span", "sev", it.kind, li); li.append(" " + it.text); });
      link(DATA + "digest.md", "Markdown brief for sharing →", card, "small");
    }
  } catch (e) { /* optional */ }
  if (idx.mode === "synthetic") h("p", "small muted", "This preview was built from the synthetic corridor with an injected signal failure and missing trips; the pipeline replaces it with live MTA data on each run.", app);
}
function tile(parent, label, value, delta) { const t = h("div", "tile", null, parent); h("div", "label", label, t); h("div", "value", value, t); if (delta) h("div", "delta", delta, t); return t; }

// ---------------------------------------------------------------- station report
async function station(idx, id) {
  const meta = idx.targets.find(t => t.id === id);
  if (!meta) { h("div", "empty", "Unknown station.", app); return; }
  const r = await load(`reports/${id}.json`);
  const head = h("div", null, null, app);
  const t1 = h("h1", null, null, head); meta.routes.forEach(x => routeBullet(x, t1)); t1.append(` ${meta.station_name || meta.station}`);
  h("div", "secondary", `${meta.direction === "N" ? "Uptown / northbound" : "Downtown / southbound"} arrivals · ${meta.label}`, head);
  if (r.status !== "ok") {
    const e = h("div", "empty", null, app); e.style.marginTop = "1rem";
    h("div", null, "Collecting data for this platform.", e); h("div", "small muted", r.message || "", e);
    h("p", "small secondary", "The hourly pipeline appends observed arrivals; a baseline comparison appears after about two days of coverage.", e);
    return;
  }
  const tiles = h("div", "tiles", null, app); tiles.style.marginTop = "1rem";
  const noBase = !r.comparisons || r.comparisons.length === 0;
  const sev = tile(tiles, "Severity (0-100)", noBase ? "n/a" : r.severity.score, noBase ? "needs a baseline" : ""); if (!noBase) badge(r.severity.label, sevClass(r.severity.label), sev);
  const hasImpact = r.impact && r.impact.ridership_source !== "no_baseline";
  tile(tiles, "Extra journey time", hasImpact ? `${fmt.num(r.impact.passenger_hours_per_day)} pax-h/day` : "n/a", hasImpact ? `${fmt.num1(r.impact.extra_wait_min_per_rider)} min per rider · ${fmt.compact(r.impact.riders_per_day_exposed)} riders/day` : "needs a baseline");
  tile(tiles, "Focus hours", r.focus_hours?.length ? hoursText(r.focus_hours) : "all hours", r.focus_mode === "detected" ? "detected automatically" : r.focus_mode);
  tile(tiles, "Arrivals analysed", `${fmt.num(r.coverage.arrivals_in_window)} / ${fmt.num(r.coverage.arrivals_in_baseline)}`, "window / baseline");
  const v = h("div", `verdict ${r.comparisons.some(c => c.direction === "worse") ? "worse" : "ok"}`, r.verdict, app);
  h("div", "small muted", `Window ${r.window.start.slice(0, 16).replace("T", " ")} → ${r.window.end.slice(0, 16).replace("T", " ")} · baseline ${r.baseline.start.slice(0, 10)} → ${r.baseline.end.slice(0, 10)}`, app);

  // Where / Why summary
  const two = h("div", "grid-2", null, app); two.style.marginTop = "1rem";
  const whereCard = h("div", "card", null, two); h("div", "kicker", "Where the delay originates", whereCard);
  if (!r.ranked_locations.length) h("p", "small secondary", "No upstream data yet; the upstream lenses need arrivals at the stops before this platform.", whereCard);
  r.ranked_locations.forEach(l => { h("h3", null, `${causeName(l.cause)} · support ${l.score.toFixed(2)}`, whereCard); const ul = h("ul", "evidence small", null, whereCard); l.evidence.forEach(e => h("li", null, e, ul)); });
  const whyCard = h("div", "card", null, two); h("div", "kicker", "Why (ranked causes)", whyCard);
  if (!r.ranked_causes.length) h("p", "small secondary", "No cause could be isolated from the available evidence.", whyCard);
  r.ranked_causes.slice(0, 5).forEach((c, i) => { h("h3", null, `${i + 1}. ${causeName(c.cause)} · support ${c.score.toFixed(2)} · ${c.lenses.join(", ")}`, whyCard); const ul = h("ul", "evidence small", null, whyCard); c.evidence.forEach(e => h("li", null, e, ul)); });

  // What changed
  if (r.comparisons?.length) {
    h("h2", null, `What changed${r.focus_mode === "detected" ? " (focus hours)" : ""}`, app);
    comparisonTable(r.comparisons, app);
  } else {
    h("h2", null, "What changed", app);
    h("div", "empty", "No baseline period yet. Comparisons, focus-hour detection and severity appear once about two days of arrivals have been collected.", app);
  }
  if (r.comparisons_all_hours?.length) { const d = h("details", null, null, app); h("summary", null, "All hours", d); comparisonTable(r.comparisons_all_hours, d); }

  // When
  h("h2", null, "When", app);
  const whenGrid = h("div", "grid-2", null, app);
  if (r.hour_table?.length) {
    const c1 = h("div", "card", null, whenGrid);
    const ht = r.hour_table;
    barChart(c1, { title: "Problem rate by hour", subtitle: "share of arrivals that were late (≥5 min) or followed a gap (≥1.5× scheduled headway)", categories: ht.map(x => String(x.hour).padStart(2, "0")),
      series: [{ name: "window", values: ht.map(x => x.problem_rate_window) }, { name: "baseline", values: ht.map(x => x.problem_rate_baseline), color: getComputedStyle(document.documentElement).getPropertyValue("--de-emphasis").trim() }], format: fmt.pct, labelEvery: 2 });
    const c2 = h("div", "card", null, whenGrid);
    barChart(c2, { title: "Mean lateness by hour", subtitle: "minutes behind the scheduled slot", categories: ht.map(x => String(x.hour).padStart(2, "0")),
      series: [{ name: "window", values: ht.map(x => x.lateness_mean_window_sec) }, { name: "baseline", values: ht.map(x => x.lateness_mean_baseline_sec), color: getComputedStyle(document.documentElement).getPropertyValue("--de-emphasis").trim() }], format: fmt.min, labelEvery: 2 });
  } else if (r.hour_pattern?.length) {
    const c1 = h("div", "card", null, whenGrid);
    barChart(c1, { title: "Problem rate by hour (window)", categories: r.hour_pattern.map(x => String(x.hour).padStart(2, "0")), series: [{ name: "problem rate", values: r.hour_pattern.map(x => x.problem_rate) }], format: fmt.pct, labelEvery: 2 });
  }
  if (r.bucket_grid?.length) {
    const c3 = h("div", "card", null, app); c3.style.marginTop = "1rem";
    const days = [...new Set(r.bucket_grid.map(b => b.service_date))].sort(), hours = [...Array(24).keys()];
    const lookup = new Map(r.bucket_grid.map(b => [`${b.service_date}|${b.hour}`, b.problem_share]));
    heatmap(c3, { title: "Problem share by day and hour", subtitle: "darker = larger share of problem arrivals; blank = no service observed", rows: days.map(d => d.slice(5)), cols: hours.map(x => String(x).padStart(2, "0")),
      values: days.map(d => hours.map(x => lookup.has(`${d}|${x}`) ? lookup.get(`${d}|${x}`) : null)), rowLabelEvery: Math.max(1, Math.ceil(days.length / 14)) });
  }
  // Trend
  if (r.daily_series?.length > 2) {
    h("h2", null, "Trend", app);
    const ds = r.daily_series, firstW = ds.findIndex(d => d.in_window);
    const g = h("div", "grid-2", null, app);
    lineChart(h("div", "card", null, g), { title: "Additional platform time per day", subtitle: "extra expected wait vs a perfectly regular schedule" + (r.focus_hours?.length ? ` (${hoursText(r.focus_hours)})` : ""), x: ds.map(d => d.date.slice(5)),
      series: [{ name: "APT", values: ds.map(d => d.apt_sec) }], format: fmt.min, bands: firstW > 0 ? [{ from: firstW, to: ds.length - 1, label: "window" }] : [] });
    lineChart(h("div", "card", null, g), { title: "Problem share per day", x: ds.map(d => d.date.slice(5)),
      series: [{ name: "problem share", values: ds.map(d => d.problem_share) }], format: fmt.pct, bands: firstW > 0 ? [{ from: firstW, to: ds.length - 1, label: "window" }] : [] });
    const tl = h("ul", "small secondary", null, app);
    (r.trends || []).forEach(t => h("li", null, `${t.metric}: ${t.direction} over ${t.n_days} days (Kendall τ ${t.kendall_tau == null ? "–" : t.kendall_tau.toFixed(2)}, p=${t.p_value == null ? "–" : t.p_value.toFixed(3)})${t.changepoint_date ? `; change point ${t.changepoint_date}` : ""}`, tl));
  }
  // Recommendations
  h("h2", null, "How to avoid or mitigate", app);
  const recCard = h("div", "card", null, app);
  for (const aud of ["operator", "rider", "monitoring"]) {
    const items = r.recommendations.filter(x => x.audience === aud); if (!items.length) continue;
    h("div", "kicker", aud, recCard).style.marginTop = ".6rem";
    items.forEach(x => { const row = h("div", "rec", null, recCard); h("div", "pri", `P${x.priority}`, row); const body = h("div", null, null, row); h("div", null, x.action, body); h("div", "why", `Why: ${x.rationale} Expected: ${x.expected_effect}`, body); const pill = h("span", "pill", causeName(x.cause), body); });
  }
  // Evidence detail + coverage
  const det = h("details", null, null, app); det.style.marginTop = "1rem"; h("summary", null, "All evidence and data coverage", det);
  const ev = h("table", null, null, h("div", "table-wrap", null, det)); const tr = h("tr", null, null, h("thead", null, null, ev)); ["lens", "kind", "cause", "share", "lift", "confidence", "summary"].forEach(x => h("th", null, x, tr));
  const tb = h("tbody", null, null, ev); r.evidence.forEach(e => { const row = h("tr", null, null, tb); [e.lens, e.kind, causeName(e.cause), e.share_explained == null ? "–" : fmt.pct(e.share_explained), e.lift == null ? "–" : `${e.lift.toFixed(1)}×`, e.confidence.toFixed(2), e.summary].forEach(x => h("td", null, x, row)); });
  const cov = h("ul", "small secondary", null, det);
  Object.entries(r.coverage).forEach(([k, v]) => h("li", null, `${k}: ${typeof v === "object" ? JSON.stringify(v) : v}`, cov));
  (r.caveats || []).forEach(c => h("li", null, `caveat: ${c}`, cov));
  h("div", "tiny muted", `Sources: ${r.sources_used.join(", ")}`, det);

  try {
    const dw = await load("dwell.json");
    const mine = (dw.stops || []).find(x => x.stop_id === (r.target?.stop_id || r.stop_id || ""));
    if (mine) {
      h("h2", null, "Dwell time at this platform", app);
      const card = h("div", "card", null, app);
      const tiles = h("div", "tiles", null, card);
      tile(tiles, "Median dwell", `${mine.median_sec.toFixed(0)} s`, `p90 ${mine.p90_sec.toFixed(0)} s · ${mine.n} stops observed`);
      tile(tiles, "Peak vs off-peak", mine.peak_median_sec != null && mine.offpeak_median_sec != null ? `${mine.peak_median_sec.toFixed(0)} / ${mine.offpeak_median_sec.toFixed(0)} s` : "–", "weekday peak median / other");
      tile(tiles, "Dwells over 90 s", `${(mine.share_over_90s * 100).toFixed(0)}%`, "holds, crowding or door problems");
      const el = (dw.elasticity || []).find(x => x.stop_id === mine.stop_id);
      tile(tiles, "Crowding link", el ? `ρ ${el.correlation.toFixed(2)}` : "–", el ? el.reading : "needs hourly ridership");
      barChart(card, { title: "Median dwell by hour (seconds, lower bound from 30-second polls)", categories: HOURS, series: [{ name: "median dwell", values: mine.by_hour.map(v => v == null ? 0 : v) }], format: fmt.sec, labelEvery: 3, height: 200 });
    }
  } catch (e) { /* optional */ }
}
function comparisonTable(comps, parent) {
  const wrap = h("div", "table-wrap card", null, parent);
  const t = h("table", null, null, wrap); const tr = h("tr", null, null, h("thead", null, null, t));
  ["metric", "window", "baseline", "diff", "95% CI", "p", "effect", "verdict"].forEach((x, i) => h("th", i ? "num" : "", x, tr));
  const tb = h("tbody", null, null, t);
  const f = (m, v, signed) => { if (v == null) return "–"; if (m.endsWith("_share") || m === "service_delivered") return `${signed && v > 0 ? "+" : ""}${(v * 100).toFixed(1)}%`; if (m.endsWith("_sec")) return `${signed && v > 0 ? "+" : ""}${(v / 60).toFixed(2)} min`; return `${signed && v > 0 ? "+" : ""}${v.toFixed(2)}`; };
  comps.forEach(c => { const row = h("tr", c.direction === "worse" ? "worse" : "", null, tb);
    [c.metric, f(c.metric, c.window_value), f(c.metric, c.baseline_value), f(c.metric, c.diff, true), `[${f(c.metric, c.ci_lo)}, ${f(c.metric, c.ci_hi)}]`, c.p_value == null ? "–" : (c.p_value < 0.001 ? "<0.001" : c.p_value.toFixed(3)), c.effect_size == null ? "–" : c.effect_size.toFixed(2), c.direction].forEach((x, i) => h("td", i ? "num" : "", x, row)); });
}

// ---------------------------------------------------------------- lines
async function lines(idx, line) {
  try {
    const sc = await load("scorecard.json");
    if ((sc.rows || []).length) {
      h("h2", null, "Network scorecard (observed trains, recent history)", app);
      h("p", "small secondary", `${fmt.compact(sc.n_arrivals)} observed stop arrivals over ${sc.days.toFixed(0)} days, from the all-stops collection and the subwaydata.nyc backfill. Sorted by share of arrivals ≥5 min late and running-time loss.`, app);
      const wrap = h("div", "table-wrap card", null, app); const t = h("table", null, null, wrap); const tr = h("tr", null, null, h("thead", null, null, t));
      ["line", "dir", "trips/day", "mean late", "p90 late", "≥5 min late", "time lost / trip", "headway CV peak / off", "grew ≥3 min", "worst segment", "by hour"].forEach((x, i) => h("th", i >= 2 && i <= 8 ? "num" : "", x, tr));
      const tb = h("tbody", null, null, t);
      sc.rows.forEach(r => { const row = h("tr", null, null, tb); routeBullet(r.route, h("td", null, null, row)); h("td", null, r.direction === "N" ? "N" : "S", row);
        h("td", "num", r.trips_per_day.toFixed(0), row); h("td", "num", lateTxt(r.mean_lateness_sec), row); h("td", "num", lateTxt(r.p90_lateness_sec), row);
        h("td", "num", `${(r.share_late_5min * 100).toFixed(0)}%`, row); h("td", "num", `${(r.loss_per_trip_sec / 60).toFixed(1)} min`, row);
        h("td", "num", `${r.headway_cv_peak == null ? "–" : r.headway_cv_peak.toFixed(2)} / ${r.headway_cv_offpeak == null ? "–" : r.headway_cv_offpeak.toFixed(2)}`, row);
        h("td", "num", `${(r.share_trips_grew_3min * 100).toFixed(0)}%`, row); h("td", "small", r.worst_segment_stop ? `${r.worst_segment_stop} (+${r.worst_segment_loss_sec.toFixed(0)} s)` : "–", row);
        const sp = h("td", null, null, row); sparkline(sp, r.hourly_mean_lateness.map(v => v == null ? 0 : v), { width: 110, height: 26 }); });
      h("div", "small secondary", "Headway CV: standard deviation ÷ mean of headways at the line's busiest observed stop (0.3 is regular, 0.6+ is bunched). Time lost per trip sums the positive lateness changes along the trip. Sparkline: mean lateness by hour of day.", app);
    }
    const tr = await load("train_runs.json");
    if (tr && tr.n_pairs && tr.overall) {
      h("h2", null, "Do terminals absorb delays? Lateness carried into the next trip", app);
      const card = h("div", "card", null, app);
      const o = tr.overall;
      const tiles = h("div", "tiles", null, card);
      tile(tiles, "Terminal turns matched", fmt.compact(tr.n_pairs), `median layover ${o.median_layover_min.toFixed(0)} min`);
      tile(tiles, "Arrived ≥5 min late", `${(o.share_late_in * 100).toFixed(0)}%`, "of inbound trips at the terminal");
      tile(tiles, "…and left late again", o.share_late_out_given_late_in != null ? `${(o.share_late_out_given_late_in * 100).toFixed(0)}%` : "–", o.median_recovered_sec != null ? `median ${(o.median_recovered_sec / 60).toFixed(1)} min recovered at the terminal` : "");
      tile(tiles, "Carry-over slope", o.carry_slope != null ? o.carry_slope.toFixed(2) : "–", "extra seconds late departing per second late arriving");
      if ((tr.by_terminal || []).length) {
        const wrap = h("div", "table-wrap", null, card); const t = h("table", null, null, wrap); const trh = h("tr", null, null, h("thead", null, null, t));
        ["line", "terminal", "turns", "layover", "late in", "late out | late in", "recovered"].forEach((x, i) => h("th", i >= 2 ? "num" : "", x, trh)); const tb = h("tbody", null, null, t);
        tr.by_terminal.slice(0, 20).forEach(x => { const r = h("tr", null, null, tb); routeBullet(x.route, h("td", null, null, r)); h("td", null, x.terminal_name, r); h("td", "num", String(x.n), r); h("td", "num", `${x.median_layover_min.toFixed(0)} min`, r);
          h("td", "num", `${(x.share_late_in * 100).toFixed(0)}%`, r); h("td", "num", x.share_late_out_given_late_in == null ? "–" : `${(x.share_late_out_given_late_in * 100).toFixed(0)}%`, r); h("td", "num", x.median_recovered_sec == null ? "–" : `${(x.median_recovered_sec / 60).toFixed(1)} min`, r); });
      }
      h("div", "small secondary", "Trips are chained first-in-first-out at each terminal (a trip ending at the station is matched with the next trip of the same line leaving it the other way within 40 min). A high 'late out | late in' share means the scheduled recovery time is too short for the delays that actually arrive.", card);
    }
  } catch (e) { /* scorecard is optional */ }
  const data = await load("lines.json");
  h("h1", null, "Line trends from MTA Open Data", app);
  h("p", "secondary", "Monthly trains delayed by reported cause, month-over-month and year-over-year change, category over-index versus the system, and customer journey metrics.", app);
  const names = Object.keys(data.lines || {}).sort();
  if (!names.length) { h("div", "empty", "Open Data context has not been pulled yet.", app); return; }
  const filters = h("div", "filters", null, app);
  h("label", "small secondary", "Line", filters);
  const sel = h("select", null, null, filters); h("option", null, "System overview", sel).value = "";
  names.forEach(n => { const o = h("option", null, n, sel); o.value = n; });
  sel.value = line && names.includes(line) ? line : ""; sel.addEventListener("change", () => { location.hash = sel.value ? `#/lines/${sel.value}` : "#/lines"; });
  const cats = data.categories, months = data.months, mlabels = months.map(m => m.slice(2));
  if (!sel.value) {
    const s = data.system;
    const tiles = h("div", "tiles", null, app);
    tile(tiles, `Trains delayed, ${s.latest_month}`, fmt.compact(s.latest_total), `${pctChange(s.mom_change)} vs prior month · ${pctChange(s.yoy_change)} vs last year`);
    const card = h("div", "card", null, app); card.style.marginTop = "1rem";
    barChart(card, { title: "System: trains delayed per month by reported cause", categories: mlabels, stacked: true, labelEvery: 3, height: 280,
      series: cats.map((c, i) => ({ name: c, values: s.monthly_by_category[c] || [] })) });
    h("h2", null, "Lines ranked by delays (3-month average)", app);
    const wrap = h("div", "table-wrap card", null, app); const t = h("table", null, null, wrap);
    const tr = h("tr", null, null, h("thead", null, null, t)); ["line", "avg / month", "latest", "vs prior month", "vs last year", "top cause", "trend"].forEach((x, i) => h("th", i && i < 5 ? "num" : "", x, tr));
    const tb = h("tbody", null, null, t);
    s.ranking.forEach(rk => { const row = h("tr", null, null, tb); const c0 = h("td", null, null, row); routeBullet(rk.line, c0); link(`#/lines/${rk.line}`, rk.line, c0);
      [fmt.compact(rk.avg_3m), fmt.compact(rk.latest_total), pctChange(rk.mom_change), pctChange(rk.yoy_change)].forEach(x => h("td", "num", x, row)); h("td", null, rk.top_category || "–", row);
      const sp = h("td", null, null, row); sparkline(sp, (data.lines[rk.line]?.monthly_total || []).slice(-12)); });
    return;
  }
  const L = data.lines[sel.value];
  const tiles = h("div", "tiles", null, app);
  const tl = tile(tiles, `Trains delayed, ${data.system.latest_month}`, fmt.compact(L.latest_total), `${pctChange(L.mom_change)} vs prior month · ${pctChange(L.yoy_change)} vs last year`);
  const top = L.category_mix_3m[0]; if (top) tile(tiles, "Top reported cause (3 months)", top.category, `${fmt.pct(top.line_share)} of delays · ${top.over_index == null ? "–" : top.over_index.toFixed(1) + "×"} system share`);
  if (L.major_incidents_total_12m != null) tile(tiles, "Major incidents (12 months)", fmt.num(L.major_incidents_total_12m), "incidents delaying 50+ trains");
  const card = h("div", "card", null, app); card.style.marginTop = "1rem";
  barChart(card, { title: `Line ${sel.value}: trains delayed per month by reported cause`, categories: mlabels, stacked: true, labelEvery: 3, height: 280,
    series: cats.map(c => ({ name: c, values: L.monthly_by_category[c] || [] })) });
  const g = h("div", "grid-2", null, app); g.style.marginTop = "1rem";
  const mix = h("div", "card", null, g); h("div", "kicker", "Cause mix vs system (last 3 months)", mix);
  const t = h("table", null, null, h("div", "table-wrap", null, mix)); const tr = h("tr", null, null, h("thead", null, null, t)); ["category", "line", "system", "over-index"].forEach((x, i) => h("th", i ? "num" : "", x, tr));
  const tb = h("tbody", null, null, t); L.category_mix_3m.forEach(c => { const row = h("tr", null, null, tb); h("td", null, c.category, row); h("td", "num", fmt.pct(c.line_share), row); h("td", "num", fmt.pct(c.system_share), row); h("td", "num", c.over_index == null ? "–" : `${c.over_index.toFixed(2)}×`, row); });
  if (L.journey?.length) {
    const byMonth = {}; L.journey.forEach(j => { (byMonth[j.month] ||= []).push(j); });
    const jm = Object.keys(byMonth).sort(); const pick = (k) => jm.map(m => { const rows = byMonth[m]; const peak = rows.find(x => (x.period || "").toLowerCase().includes("peak") && !(x.period || "").toLowerCase().includes("off")) || rows[0]; return peak ? peak[k] : null; });
    const jc = h("div", "card", null, g);
    lineChart(jc, { title: "Additional platform and train time (peak)", subtitle: "minutes per customer, MTA Customer Journey metrics", x: jm.map(m => m.slice(2)), series: [{ name: "platform time", values: pick("apt_min") }, { name: "train time", values: pick("att_min") }], format: fmt.num1 });
    const jc2 = h("div", "card", null, g);
    lineChart(jc2, { title: "Customer journey time performance (peak)", subtitle: "share of journeys within 5 minutes of schedule", x: jm.map(m => m.slice(2)), series: [{ name: "CJTP", values: pick("cjtp") }], format: fmt.pct, yMin: 0.5 });
  }
  if (L.major_incidents) {
    const mc = h("div", "card", null, app); mc.style.marginTop = "1rem";
    const keys = Object.keys(L.major_incidents).slice(0, 8);
    barChart(mc, { title: `Line ${sel.value}: major incidents per month by category`, subtitle: "incidents that delayed 50 or more trains", categories: mlabels, stacked: true, labelEvery: 3, series: keys.map(k => ({ name: k, values: L.major_incidents[k] })) });
  }
}

// ---------------------------------------------------------------- alerts
async function alerts() {
  const data = await load("alerts.json");
  h("h1", null, "Service alerts", app);
  h("p", "secondary", `Alerts seen by the collector in the last 24 hours (${data.alerts.length}); active now are listed first. Unplanned delay alerts are tagged with a cause category used by the attribution lenses; planned changes and informational notices are shown separately.`, app);
  if (!data.alerts.length) { h("div", "empty", "No alerts recorded yet.", app); return; }
  const filters = h("div", "filters", null, app);
  const sel = h("select", null, null, filters); [["delay", "Unplanned delays"], ["planned", "Planned changes"], ["notice", "Notices"], ["all", "All"]].forEach(([v, l]) => { const o = h("option", null, l, sel); o.value = v; });
  const search = h("input", null, null, filters); search.type = "search"; search.placeholder = "route or text";
  const wrap = h("div", "table-wrap card", null, app);
  const draw = () => {
    wrap.replaceChildren(); const t = h("table", null, null, wrap); const tr = h("tr", null, null, h("thead", null, null, t));
    ["status", "routes", "type", "cause", "since", "header"].forEach(x => h("th", null, x, tr)); const tb = h("tbody", null, null, t);
    const q = search.value.trim().toLowerCase();
    data.alerts.filter(a => sel.value === "all" || (a.kind || (a.planned ? "planned" : "delay")) === sel.value).filter(a => !q || a.header.toLowerCase().includes(q) || a.routes.some(r => r.toLowerCase() === q))
      .sort((a, b) => (b.active_now - a.active_now) || ((b.active_start || 0) - (a.active_start || 0)))
      .forEach(a => { const row = h("tr", null, null, tb); const s = h("td", null, null, row); badge(a.active_now ? "active" : "ended", a.active_now ? (a.planned ? "s-moderate" : "s-high") : "s-na", s);
        const rc = h("td", null, null, row); a.routes.forEach(r => routeBullet(r, rc)); h("td", null, a.alert_type || "–", row); h("td", null, a.kind === "notice" ? "notice" : (a.planned ? "planned" : causeName(a.cause_category)), row);
        h("td", "small", a.active_start ? new Date(a.active_start * 1000).toLocaleString(undefined, { dateStyle: "short", timeStyle: "short" }) : "–", row); h("td", null, a.header, row); });
  };
  sel.addEventListener("change", draw); search.addEventListener("input", draw); draw();

  try {
    const es = await load("event_study.json");
    if (es && es.n_alerts && es.overall) {
      h("h2", null, "What happens around an alert", app);
      const card = h("div", "card", null, app);
      const o = es.overall;
      const tiles = h("div", "tiles", null, card);
      tile(tiles, "Alerts studied", String(es.n_alerts), "unplanned alerts with observed trains");
      tile(tiles, "Detection lag", o.detection_lag_min != null ? `${o.detection_lag_min.toFixed(0)} min` : "–", o.share_with_onset_before != null ? `${(o.share_with_onset_before * 100).toFixed(0)}% of alerts: lateness rose ≥2 min before the post` : "");
      tile(tiles, "Peak excess", o.peak_excess_sec != null ? `${(o.peak_excess_sec / 60).toFixed(1)} min` : "–", "median over alerts vs the pre-alert level");
      tile(tiles, "Recovery", o.recovery_min != null ? `${o.recovery_min.toFixed(0)} min` : "–", o.share_recovered != null ? `${(o.share_recovered * 100).toFixed(0)}% recovered within 2 h` : "");
      const series = [{ name: "all causes", values: o.mean_curve }, ...es.by_cause.slice(0, 4).map(c => ({ name: causeName(c.cause), values: c.mean_curve }))];
      lineChart(card, { title: "Mean lateness of the route's trains around the alert (minutes before/after it was posted)", x: o.bins.map(b => `${b >= 0 ? "+" : ""}${b}`), series, format: fmt.sec, labelEvery: 4, yMin: 0 });
      h("div", "small secondary", "0 is the moment the MTA posted the alert. Lateness rising well before 0 means the trains showed the problem first; the tail after the peak is how long service takes to recover.", card);
    }
  } catch (e) { /* optional */ }
}

// ---------------------------------------------------------------- data / about
async function dataPage(idx) {
  const st = await load("status.json");
  h("h1", null, "Data pipeline and sources", app);
  h("p", "secondary", "A GitHub Actions job polls the MTA GTFS-Realtime feeds for the monitored platforms and their upstream stops, derives observed arrivals, pulls Open Data context, re-runs the analyses and publishes this site.", app);
  const tiles = h("div", "tiles", null, app);
  tile(tiles, "Observed arrivals", fmt.compact(st.arrivals_total)); tile(tiles, "Days with data", st.days_with_data); tile(tiles, "Pipeline runs logged", (st.runs || []).length);
  tile(tiles, "Static GTFS", st.gtfs?.feed_version || "–", st.gtfs ? `${st.gtfs.stations} stations · ${st.gtfs.routes} routes` : "");
  const ds = st.datasets || {};
  if (Object.keys(ds).length) {
    const t2 = h("div", "tiles", null, app); t2.style.marginTop = ".6rem";
    tile(t2, "Network-wide arrivals", fmt.compact(ds.network_arrivals || 0), `${ds.network_days || 0} days · ${Object.entries(ds.network_sources || {}).map(([k, v]) => `${k} ${fmt.compact(v)}`).join(", ") || "own collection + subwaydata.nyc"}`);
    tile(t2, "ETA samples", fmt.compact(ds.eta_samples || 0), "feed predictions at 1–12 stops ahead");
    tile(t2, "Dwell estimates", fmt.compact(ds.dwells || 0), `from vehicle positions · ${fmt.compact(ds.holds || 0)} holds network-wide`);
    tile(t2, "Scored live forecasts", fmt.compact(ds.forecast_eval || 0), "predicted arrivals matched to what happened");
    tile(t2, "Alert archive rows", fmt.compact(ds.alerts_archive_rows || 0), `${fmt.compact(ds.events_rows || 0)} event/news rows`);
  }
  const days = Object.keys(st.arrivals_per_day || {}).sort();
  if (days.length) { const c = h("div", "card", null, app); c.style.marginTop = "1rem"; barChart(c, { title: "Observed arrivals per day", categories: days.map(d => d.slice(5)), series: [{ name: "arrivals", values: days.map(d => st.arrivals_per_day[d]) }], labelEvery: Math.max(1, Math.ceil(days.length / 12)) }); }
  h("h2", null, "Recent runs", app);
  const wrap = h("div", "table-wrap card", null, app); const t = h("table", null, null, wrap); const tr = h("tr", null, null, h("thead", null, null, t));
  ["when", "kind", "polls", "arrivals", "errors", "notes"].forEach(x => h("th", null, x, tr)); const tb = h("tbody", null, null, t);
  (st.runs || []).slice().reverse().slice(0, 30).forEach(r => { const row = h("tr", null, null, tb); h("td", "small", dateTime(r.iso), row); h("td", null, r.kind, row); h("td", "num", r.polls ?? "–", row); h("td", "num", r.arrivals ?? "–", row); h("td", "num", r.errors ?? "–", row);
    h("td", "small", r.kind === "context" ? `ok: ${(r.ok || []).join(", ")}${Object.keys(r.failed || {}).length ? " · failed: " + Object.keys(r.failed).join(", ") : ""}` : r.kind === "backfill" ? `fetched ${(r.fetched || []).map(f => `${f.day} (${fmt.compact(f.rows)})`).join(", ") || "nothing new"}${Object.keys(r.failed || {}).length ? " · failed: " + Object.keys(r.failed).join(", ") : ""}` : (r.feeds || []).join(", "), row); });
  h("h2", null, "Sources", app);
  const sw = h("div", "table-wrap card", null, app); const s = h("table", null, null, sw); const str = h("tr", null, null, h("thead", null, null, s)); ["key", "kind", "title", "contributes"].forEach(x => h("th", null, x, str));
  const sb = h("tbody", null, null, s); idx.sources.forEach(x => { const row = h("tr", null, null, sb); const c = h("td", "mono small", null, row); link(x.url, x.key, c); h("td", null, x.kind, row); h("td", null, x.title, row); h("td", "small", x.contributes, row); });
  h("h2", null, "Method in one paragraph", app);
  h("p", "secondary small", "Observed arrivals come from stops dropping off GTFS-Realtime trip updates. Each arrival is matched to the schedule (lateness, the trip's own scheduled headway). Per route and hour we compute gap, bunching, expected platform wait and the MTA-style additional platform time. The window is compared with the baseline using bootstrap confidence intervals, Mann-Whitney tests, Cliff's delta and a practical-magnitude threshold; a per-hour scan finds the hours that worsened. Attribution lenses then locate the origin (upstream vs. approach, run-time loss per segment) and the cause (alerts, run-time pattern, merge conflicts, terminal departures, missing trains, MTA incident categories, weather). Severity combines confidence, effect size and rider exposure; rider impact converts extra wait and lateness into passenger-hours using hourly ridership.", app);
  link("https://github.com/bdrumm/Test-22222222/blob/claude/train-delay-analysis-framework-auxzv1/docs/METHODOLOGY.md", "Full methodology", app);
}


// ---------------------------------------------------------------- live
const NY = "America/New_York";
const hhmm = ts => ts ? new Date(ts * 1000).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", timeZone: NY }) : "–";
const ageText = sec => sec < 90 ? "just now" : sec < 3600 ? `${(sec / 60).toFixed(0)} min ago` : sec < 86400 ? `${(sec / 3600).toFixed(1)} h ago` : `${(sec / 86400).toFixed(0)} days ago`;
const minsFromNow = (ts, now) => ts ? `${Math.max(0, (ts - now) / 60).toFixed(0)} min` : "–";
const lateTxt = s => s == null ? "–" : (Math.abs(s) < 60 ? "on time" : `${s > 0 ? "+" : "−"}${Math.abs(s / 60).toFixed(0)} min`);
function statusChip(parent, status, label) { const c = h("span", `status-chip st-${status}`, null, parent); h("span", "dot", null, c); c.append(label); h("span", "st", status, c); return c; }

const hhmmss = ts => ts ? new Date(ts * 1000).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", second: "2-digit", timeZone: NY }) : "–";
const flag = (parent, text, cls) => h("span", `flag ${cls}`, text, parent);
const posText = p => !p ? "–" : `${p.status === "STOPPED_AT" ? "at" : p.status === "INCOMING_AT" ? "arriving" : "→"} ${p.stop_name || p.stop_id || "?"}${p.since_sec != null && p.since_sec >= 60 ? ` · ${(p.since_sec / 60).toFixed(0)} min` : ""}`;
function posFlags(cell, p, corroboration, extra = {}) {
  if (p && p.holding) flag(cell, "holding", "crit"); if (p && p.stalled) flag(cell, "stalled", "crit");
  if (corroboration === "feed_optimistic") flag(cell, "feed optimistic", "warn"); if (extra.track_changed) flag(cell, "track change", "info");
  if (extra.gap) flag(cell, "gap", "warn"); if (extra.bunched) flag(cell, "bunched", "info");
}

// Browser-side polling of the MTA feeds (every 30 s) rendered above the pipeline snapshot.
function setupClientLive(ctl, box, { render = renderClientBoard, feedKeys = null } = {}) {
  const btn = h("button", "icon-btn live-toggle", null, ctl); btn.title = "Poll the MTA GTFS-Realtime feeds from this browser every 30 seconds";
  let on = false; try { on = localStorage.getItem("rtClient") === "1"; } catch {}
  const setLabel = () => { btn.textContent = on ? "● live feeds: on" : "○ live feeds: off"; btn.classList.toggle("on", on); };
  const stop = () => { if (clientLive) { clientLive.stop(); clientLive = null; } box.replaceChildren(); };
  const start = () => {
    stop(); h("div", "small secondary", "Connecting to the MTA feeds…", box);
    clientLive = createClientLive({ base: DATA, feedKeys, onUpdate: (board, schedule, feeds) => render(box, board, schedule, feeds),
      onError: e => { box.replaceChildren(); const em = h("div", "empty", null, box); h("div", null, "Could not read the MTA feeds from this browser.", em);
        h("div", "small muted", `${e.message || e}. The feeds are fetched directly from api-endpoint.mta.info; an ad blocker, a corporate proxy or a missing data/client_schedule.json can block them. The pipeline snapshot still works.`, em); } });
    clientLive.start();
  };
  btn.addEventListener("click", () => { on = !on; try { localStorage.setItem("rtClient", on ? "1" : "0"); } catch {} setLabel(); if (on) start(); else stop(); });
  setLabel(); if (on) start();
}
async function renderLineLive(box, board, schedule, feeds, route, direction) {
  let lines = null;
  try { lines = await load("client_lines.json"); } catch (e) { /* optional: lateness unavailable */ }
  const lb = lineBoard(schedule, (lines && lines.lines || {})[`${route}_${direction}`] || [], feeds, route, direction, board.now);
  box.replaceChildren();
  if (!lb) { h("div", "small secondary", `No stop sequence for ${route} ${direction} in data/client_schedule.json (rebuild the site).`, box); return; }
  const now = board.now, card = h("div", "card", null, box);
  const hd = h("div", "row between", null, card);
  h("h3", null, "Live from the MTA feeds: every train on the line now", hd);
  h("span", "small secondary", `${board.demo ? "recorded snapshot replayed at" : "polled"} ${hhmmss(now)} ET · every 30 s · ${lb.trains.length} trains · ${lb.n_holding} holding · ${lb.n_stalled} stalled · feed optimistic for ${lb.n_feed_optimistic}`, hd);
  const held = t => t.position && (t.position.holding || t.position.stalled);
  const legs = [{ stops: lb.stops, trains: lb.trains.map(t => ({ trip_id: t.trip_id, train_id: t.train_id, route_id: route, points: t.points, lateness_sec: t.effective_lateness_sec ?? t.lateness_sec, kind: held(t) ? "live-hold" : "live" })) }];
  const markers = lb.trains.filter(t => t.position && t.position.stop_idx != null).map(t => { const p = t.position, moving = p.status !== "STOPPED_AT";
    return { leg: 0, stop: moving && p.stop_idx > 0 ? p.stop_idx - 0.5 : p.stop_idx, ts: now, color: held(t) ? "var(--status-critical)" : moving ? "var(--status-good)" : "var(--status-warning)",
      label: `${route} ${(t.train_id || t.trip_id).trim()}`, rows: [["position", `${posText(p)}`], ["lateness", lateTxt(t.effective_lateness_sec ?? t.lateness_sec)], ["feed vs position", t.corroboration.replace(/_/g, " ")]] }; });
  stringline(card, { title: `${route} ${direction === "N" ? "northbound" : "southbound"}: reported positions and the feed's projection for the next hour`,
    subtitle: "dots: where each train is now (green moving, amber stopped, red holding or stalled); dashed: the feed's ETAs; red dashed: held or stalled trains (lateness from the position when it proves the feed optimistic)",
    legs, now, horizonSec: 3600, backSec: 600, routeColor: () => ROUTE_COLORS[route] || null, rowH: 12, markers });
  if (lb.trains.length) {
    const wrap = h("div", "table-wrap", null, card); const tb = h("table", "tiny", null, wrap); const tr = h("tr", null, null, h("thead", null, null, tb));
    ["train", "position", "next stop", "ETA", "vs schedule", "flags"].forEach((x, i) => h("th", i === 3 || i === 4 ? "num" : "", x, tr)); const body = h("tbody", null, null, tb);
    lb.trains.forEach(t => { const row = h("tr", null, null, body); const c0 = h("td", null, null, row); routeBullet(route, c0); c0.append(` ${(t.train_id || t.trip_id).trim()}`);
      h("td", "small", t.position ? posText(t.position) : "–", row); h("td", "small", t.next_name, row); h("td", "num eta", hhmm(t.eta_ts), row);
      const lc = h("td", "num", (t.sched_method === "nearest" ? "~" : "") + lateTxt(t.lateness_sec) + (t.corroboration === "feed_optimistic" ? ` → ${lateTxt(t.effective_lateness_sec)}` : ""), row); if (t.sched_method === "nearest") lc.title = "matched to the nearest scheduled trip (the realtime trip id is not in the timetable)";
      posFlags(h("td", "small", null, row), t.position, t.corroboration, { track_changed: t.track_changed }); });
  } else h("div", "small secondary", "No train of this line is under way right now.", card);
  h("div", "tiny muted", "Computed in this browser from the feed's trip updates and vehicle positions; lateness compares the ETA at the next stop with the timetable (scheduled time at the trip's last stop minus the canonical running times).", card);
}

function renderClientBoard(box, board, schedule) {
  box.replaceChildren();
  const now = board.now, C = schedule.constants || {};
  const hd = h("div", "row between", null, box);
  h("h2", null, "Live from the MTA feeds", hd);
  const feedTxt = (board.feeds || []).map(f => `${f.key.replace(/^nyct-?/, "") || "1-7"} ${f.feed_ts ? `${Math.max(0, now - f.feed_ts).toFixed(0)} s` : "?"}`).join(" · ");
  h("span", "small secondary", `${board.demo ? "recorded snapshot replayed at " : "polled "}${hhmmss(now)} ET · every 30 s · feed age: ${feedTxt}`, hd);
  const tiles = h("div", "tiles", null, box);
  tile(tiles, "Trips in the feeds", board.summary.trips, `${board.summary.vehicles} with a reported position`);
  tile(tiles, "Holding", board.summary.holding, `stopped ≥ ${((C.hold_sec || 150) / 60).toFixed(1)} min at a station`);
  tile(tiles, "Stalled", board.summary.stalled, "between stations longer than scheduled");
  tile(tiles, "Feed optimistic", board.summary.feed_optimistic, "position proves the ETA too early");
  const grid = h("div", "grid-2", null, box);
  for (const t of board.targets) {
    const card = h("div", "card", null, grid);
    const th = h("div", "row between", null, card); const tt = h("div", null, null, th); t.routes.forEach(r => routeBullet(r, tt)); h("strong", null, ` ${t.station_name || t.label || t.id}`, tt);
    if (t.disturbed) { const w = h("span", "status-chip st-degraded", null, th); h("span", "dot", null, w); w.append("hold upstream"); }
    h("div", "small muted", `${t.direction === "N" ? "Uptown / northbound" : "Downtown / southbound"} · platform ${t.stop_id}`, card);
    const pr = h("div", "row small secondary", null, card); pr.style.margin = ".4rem 0";
    Object.entries(t.per_route).forEach(([r, v]) => { const sp = h("span", null, null, pr); routeBullet(r, sp); sp.append(v.next_eta_ts ? ` next in ${minsFromNow(v.next_eta_ts, now)}` : " none in the next hour"); if (v.sched_headway_sec) sp.append(` (every ${(v.sched_headway_sec / 60).toFixed(0)})`); });
    if (!t.arrivals.length) { h("div", "small secondary", t.n_sched_today ? "No trains predicted for this platform in the next hour." : "No timetable shipped for today: rebuild the site to refresh data/client_schedule.json.", card); continue; }
    const wrap = h("div", "table-wrap", null, card); const tb = h("table", null, null, wrap); const tr = h("tr", null, null, h("thead", null, null, tb));
    const cols = ["route", "ETA", "in", "vs schedule", "position", "flags"]; if (t.disturbed) cols.splice(3, 0, "if hold persists");
    cols.forEach(x => h("th", ["ETA", "in", "vs schedule", "if hold persists"].includes(x) ? "num" : "", x, tr));
    const body = h("tbody", null, null, tb);
    t.arrivals.slice(0, 8).forEach(a => {
      const row = h("tr", a.gap ? "gap-row" : "", null, body); const c0 = h("td", null, null, row); routeBullet(a.route, c0); if (a.train_id) { const sp = h("span", "tiny muted", ` ${a.train_id.trim()}`, c0); sp.title = "NYCT train id"; }
      h("td", "num eta", hhmm(a.eta_ts), row); h("td", "num", minsFromNow(a.eta_ts, now), row);
      if (t.disturbed) h("td", "num eta", a.hold_eta_ts && a.hold_eta_ts - a.eta_ts >= 30 ? `${hhmm(a.hold_eta_ts)} (+${((a.hold_eta_ts - a.eta_ts) / 60).toFixed(0)})` : "same", row);
      const lc = h("td", "num", (a.sched_method === "nearest" ? "~" : "") + lateTxt(a.lateness_sec) + (a.corroboration === "feed_optimistic" ? ` → ${lateTxt(a.effective_lateness_sec)}` : ""), row); if (a.sched_method === "nearest") lc.title = "matched to the nearest scheduled trip (the realtime trip id is not in the timetable)";
      h("td", "small", !a.started ? "not departed" : posText(a.position), row);
      posFlags(h("td", "small", null, row), a.position, a.corroboration, a);
    });
  }
  if (board.alerts && board.alerts.length) {
    const al = h("div", "card", null, box);
    const delays = board.alerts.filter(x => x.kind === "delay");
    h("div", "row between", null, al).append(Object.assign(h("strong", null, `Service alerts now: ${delays.length} unplanned, ${board.alerts.length - delays.length} planned or notices`), {}));
    delays.slice(0, 8).forEach(x => { const row = h("div", "rec", null, al); const rc = h("div", null, null, row); x.routes.forEach(r => routeBullet(r, rc)); const bd = h("div", null, null, row); h("div", "small", x.header, bd); h("div", "why", `${x.type || ""}${x.start ? ` · since ${hhmm(x.start)}` : ""}`, bd); });
    if (!delays.length) h("div", "small secondary", "No unplanned delay alert is active.", al);
  } else if (board.alerts_error) h("div", "tiny muted", `Service alerts could not be fetched in the browser (${board.alerts_error}); the snapshot below lists them.`, box);
  h("p", "tiny muted", `Computed in this browser from the GTFS-Realtime trip updates and vehicle positions (schedule extract from ${(schedule.generated_at || "").slice(0, 16).replace("T", " ")}, service date ${schedule.service_date}). "vs schedule" compares the feed's ETA with the timetable; "feed optimistic" means the train's reported position proves it later than its ETA implies (the arrow shows the corrected lateness); "holding" = stopped ≥ ${((C.hold_sec || 150) / 60).toFixed(1)} min, "stalled" = in transit ${((C.stall_slack_sec || 120) / 60).toFixed(0)} min longer than the scheduled run. The "if hold persists" column adds ${((C.hold_extra_sec || 600) / 60).toFixed(0)} min to held trains and keeps followers of the same route ≥ ${C.min_headway_sec || 90} s behind their leader.`, box);
}

async function live(idx) {
  const root = app; root.replaceChildren();
  const head = h("div", "row between", null, root);
  h("h1", null, "Live status", head);
  const ctl = h("div", "refresh small secondary", null, head);
  const clientBox = h("div", null, null, root);
  const snapBox = h("div", null, null, root);
  setupClientLive(ctl, clientBox);
  async function draw() {
    let d;
    try { await dataReady; const r = await fetch(DATA + "live.json", { cache: "no-store" }); if (!r.ok) throw new Error(String(r.status)); d = await r.json(); }
    catch (e) { snapBox.replaceChildren(); const em = h("div", "empty", null, snapBox); h("div", null, "No live snapshot is available yet.", em); h("div", "small muted", "The pipeline publishes data/live.json during each collection run; for continuous 30-second updates turn on the live feeds above or run `mta-insights serve` locally.", em); return; }
    if (!d.generated_ts) { snapBox.replaceChildren(); h("div", "empty", "Live snapshot is starting…", snapBox); return; }
    const now = Date.now() / 1000, age = now - d.generated_ts;
    const root = snapBox; root.replaceChildren();
    const sub = h("div", "row between", null, root);
    h("h2", null, "Model snapshot: forecasts, scenarios and downstream effects", sub);
    const rf = h("div", "refresh small secondary", null, sub);
    h("span", "age", `as of ${hhmm(d.generated_ts)} ET · ${ageText(age)} · ${d.source}`, rf);
    const btn = h("button", "icon-btn", "↻", rf); btn.title = "Refresh"; btn.addEventListener("click", draw);
    if (age > 900) h("p", "small", `This snapshot is ${ageText(age).replace(" ago", "")} old. The Pages site refreshes only while the hourly collector runs; turn on the live feeds above for 30-second updates, or run mta-insights serve.`, root).style.color = "var(--status-serious)";
    const tiles = h("div", "tiles", null, root);
    tile(tiles, "Trains in service", d.trains_total, `${d.trains_matched} matched to schedule${d.trains_scheduled_not_started ? ` · ${d.trains_scheduled_not_started} scheduled, not yet departed` : ""}`);
    if (d.positions) tile(tiles, "Holding or stalled", (d.positions.holding || 0) + (d.positions.stalled || 0), `${d.positions.n_with_position} trains with a position · feed optimistic for ${d.positions.feed_optimistic}`);
    const hl = d.holds_last_hour;
    if (hl && hl.n != null) tile(tiles, "Holds in the last hour", hl.n, hl.n ? `${hl.minutes} min held · ${Object.entries(hl.by_route).slice(0, 4).map(([r, n]) => `${r}: ${n}`).join(", ")}${hl.top_stops[0] ? ` · most at ${hl.top_stops[0].name}` : ""}` : "no train held ≥ 2.5 min outside a terminal");
    tile(tiles, "Routes good", d.summary.good); tile(tiles, "Routes degraded", d.summary.degraded); tile(tiles, "Routes disrupted", d.summary.disrupted);
    tile(tiles, "Unplanned alerts", d.alerts.length);

    // Monitored stations: forecasts and downstream effects
    const inc = (d.incidents_developing || []).filter(x => !x.error);
    if (inc.length) {
      h("h2", null, "Developing right now", root);
      const card = h("div", "card", null, root); const ul = h("ul", "findings", null, card);
      inc.forEach(x => { const li = h("li", `sev-${x.alerted ? "medium" : "high"}`, null, ul); h("span", "sev", x.kind === "stalled" || x.kind === "holding" ? `${x.kind}${x.alerted ? "" : " · no alert"}` : (x.alerted ? "alerted" : "no alert"), li); routeBullet(x.route_id, li); li.append(` ${x.text} (since ${hhmm(x.first_seen_ts)})`); });
      h("div", "small secondary", "Consecutive trains losing ≥2 min between the same two stops in the last 20 minutes, or a train whose reported position has not moved for 5+ minutes: an incident in progress, whether or not an alert has been posted.", card);
    }
    if ((d.track_changes || []).length) {
      h("h2", null, "Trains running on a different track than scheduled", root);
      const card = h("div", "card", null, root); const row = h("div", "small", null, card);
      d.track_changes.slice(0, 20).forEach(t => { const sp = h("span", null, null, row); sp.style.marginRight = ".8rem"; routeBullet(t.route_id, sp); sp.append(` ${t.train_id || t.trip_id} → ${t.next_stop_name || t.next_stop_id}`); });
      h("div", "small secondary", "The feed's actual track differs from the scheduled one: express/local swaps and reroutes that the timetable does not know about.", card);
    }
    if (d.learned_model && d.learned_model.ready) h("div", "small secondary", `ETAs below use the learned arrival model (${fmt.compact(d.learned_model.n_train)} training rows${d.learned_model.mae_model != null ? `, ${d.learned_model.mae_model.toFixed(0)} s MAE` : ""}); see the Model page.`, root).style.marginTop = ".5rem";

    h("h2", null, "Monitored platforms: next arrivals and downstream effects", root);
    const grid = h("div", "grid-2", null, root);
    for (const s of d.stations) {
      const card = h("div", "card", null, grid);
      const hd = h("div", "row between", null, card);
      const t = h("div", null, null, hd); (s.routes || []).forEach(r => routeBullet(r, t)); h("strong", null, ` ${s.station_name || s.label}`, t);
      statusChip(hd, s.status === "normal" ? "good" : s.status, "");
      h("div", "small muted", `${s.direction === "N" ? "Uptown / northbound" : "Downtown / southbound"} · platform ${s.stop_id}`, card);
      const pr = h("div", "row small secondary", null, card); pr.style.margin = ".4rem 0";
      Object.entries(s.per_route || {}).forEach(([r, v]) => { const sp = h("span", null, null, pr); routeBullet(r, sp); sp.append(v.next_eta_ts ? ` next in ${minsFromNow(v.next_eta_ts, d.generated_ts)}` : " no train in the next hour"); if (v.sched_headway_sec) sp.append(` (every ${(v.sched_headway_sec / 60).toFixed(0)})`); });
      (s.effects || []).forEach(e => { const ef = h("div", `effect ${e.severity}`, null, card); h("span", "k", e.kind.replace("_", " "), ef); ef.append(e.text); });
      if (!(s.effects || []).length) h("div", "small secondary", "No downstream effects predicted for the next hour.", card);
      const sc = s.scenarios;
      if (sc && sc.disturbed) {
        const box = h("div", "effect high", null, card); h("span", "k", "what if", box); box.append(sc.headline || "A train serving this platform is holding or stalled upstream.");
        const wrap = h("div", "table-wrap", null, card); const tb = h("table", "tiny", null, wrap); const tr = h("tr", null, null, h("thead", null, null, tb));
        ["route", "if it clears now", "as projected", "if the hold persists"].forEach((x, i) => h("th", i ? "num" : "", x, tr)); const body = h("tbody", null, null, tb);
        sc.routes.filter(r => r.hold_persists).forEach(r => { const row = h("tr", null, null, body); routeBullet(r.route, h("td", null, null, row));
          [r.clears_now, r.baseline, r.hold_persists].forEach(list => h("td", "num eta", (list || []).slice(0, 3).map(x => hhmm(x.eta_ts)).join(", ") || "–", row)); });
      } else if (sc) h("div", "tiny muted", "Forward simulation: no train serving this platform is holding or stalled; the projection above is the baseline scenario.", card);
      if ((s.arrivals || []).length) {
        const wrap = h("div", "table-wrap", null, card); wrap.style.marginTop = ".5rem";
        const tb = h("table", null, null, wrap); const tr = h("tr", null, null, h("thead", null, null, tb));
        ["route", "feed ETA", "model ETA", "range", "vs schedule", "position", "late now", "flags"].forEach((x, i) => h("th", i >= 1 && i <= 4 ? "num" : "", x, tr));
        const body = h("tbody", null, null, tb);
        s.arrivals.slice(0, 8).forEach(a => { const row = h("tr", a.gap ? "gap-row" : "", null, body); const c0 = h("td", null, null, row); routeBullet(a.route_id, c0);
          h("td", "num eta", hhmm(a.feed_eta_ts), row); const mc = h("td", "num eta", hhmm(a.model_eta_ts), row); if (a.model_source === "learned") { const b = h("span", "tiny muted", " learned", mc); b.title = "learned arrival model"; } if (a.track_changed) { const b = h("span", "tiny muted", " ⇄track", mc); b.title = "running on a different track than scheduled"; }
          h("td", "num eta small", `${hhmm(a.eta_lo_ts)}–${hhmm(a.eta_hi_ts)}`, row);
          h("td", "num", lateTxt(a.model_lateness_sec), row); h("td", "small", a.started === false ? "not departed" : a.position ? posText(a.position) : (a.now_at_stop_name || "–"), row); h("td", "num", a.started === false ? "–" : lateTxt(a.now_lateness_sec), row);
          posFlags(h("td", "small", null, row), a.position, a.position && a.position.corroboration, { track_changed: false, gap: a.gap }); });
        // headway chart
        const hw = s.arrivals.filter(a => a.headway_sec != null);
        if (hw.length >= 2) {
          const ref = Object.values(s.per_route).map(v => v.sched_headway_sec).filter(Boolean);
          const refMin = ref.length ? Math.min(...ref) / 60 : null;
          barChart(card, { title: "Predicted headways at this platform", subtitle: "minutes between consecutive arrivals (model ETA, all routes); line = scheduled headway", categories: hw.map(a => hhmm(a.model_eta_ts).replace(" ", "")), series: [{ name: "headway", values: hw.map(a => a.headway_sec / 60) }], format: fmt.num1, height: 190, labelEvery: hw.length > 8 ? 2 : 1, refLines: refMin ? [{ value: refMin, label: "scheduled" }] : [] });
        }
      }
      const m = s.model || {};
      h("div", "tiny muted", `Look-back model: ${m.n_arrivals || 0} arrivals over ${m.n_days || 0} days${m.calibrated_routes?.length ? `; ETA calibration for ${m.calibrated_routes.join(", ")}` : "; using default priors"}${m.carry_routes?.length ? `; lateness carry for ${m.carry_routes.join(", ")}` : ""}`, card).style.marginTop = ".5rem";
    }

    // System board
    h("h2", null, "System status by route and direction", root);
    const board = h("div", "card", null, root);
    const chips = h("div", "chips", null, board);
    const byRoute = {}; (d.routes || []).forEach(r => (byRoute[r.route_id] ||= []).push(r));
    Object.keys(byRoute).sort().forEach(r => { const worst = byRoute[r].some(x => x.status === "disrupted") ? "disrupted" : byRoute[r].some(x => x.status === "degraded") ? "degraded" : "good"; const c = statusChip(chips, worst, ""); routeBullet(r, c); c.insertBefore(c.lastChild.previousSibling, c.firstChild); });
    const wrap = h("div", "table-wrap", null, board); wrap.style.marginTop = ".8rem";
    const tb = h("table", null, null, wrap); const tr = h("tr", null, null, h("thead", null, null, tb));
    ["route", "dir", "status", "trains", "median lateness", "largest gap", "where", "sched headway", "bunching", "alerts"].forEach((x, i) => h("th", [3, 4, 5, 7, 8].includes(i) ? "num" : "", x, tr));
    const body = h("tbody", null, null, tb);
    (d.routes || []).forEach(r => { const row = h("tr", null, null, body); const c0 = h("td", null, null, row); routeBullet(r.route_id, c0); h("td", null, r.direction, row);
      const sc = h("td", null, null, row); statusChip(sc, r.status, ""); h("td", "num", r.trains, row); h("td", "num", lateTxt(r.median_lateness_sec), row);
      h("td", "num", r.max_gap_sec ? `${(r.max_gap_sec / 60).toFixed(0)} min${r.max_gap_ratio ? ` (${r.max_gap_ratio.toFixed(1)}×)` : ""}` : "–", row); h("td", "small", r.max_gap_stop_name || "–", row);
      h("td", "num", r.sched_headway_sec ? `${(r.sched_headway_sec / 60).toFixed(0)} min` : "–", row); h("td", "num", r.bunching_share == null ? "–" : fmt.pct(r.bunching_share), row);
      h("td", "small", (r.alert_headers || []).join(" · ") || (r.unplanned_alerts ? String(r.unplanned_alerts) : ""), row); });
    if ((d.alerts || []).length) {
      h("h2", null, "Active unplanned alerts", root);
      const ul = h("div", "card", null, root);
      d.alerts.forEach(a => { const row = h("div", "rec", null, ul); const rc = h("div", null, null, row); (a.routes || []).forEach(r => routeBullet(r, rc)); const bd = h("div", null, null, row); h("div", null, a.header, bd); h("div", "why", `${a.alert_type || ""} · ${causeName(a.cause_category)} · since ${hhmm(a.active_start)}`, bd); });
    }
    h("p", "tiny muted", "Feed ETAs come from the MTA GTFS-Realtime trip updates. The position column is the train's last reported vehicle position (at / arriving / → next stop, and how long it has been in that state); a train is holding when stopped ≥ 2.5 min and stalled when in transit 2 min longer than the scheduled run, and the feed is optimistic when the position proves the train later than its ETA implies. Model ETAs add the look-back calibration (how much ETAs at this lead time slipped historically at this platform) and the historical effect of active alerts; the range is the p10–p90 of past ETA error. Route status: disrupted = a Delays/Suspended alert, a gap ≥ 2.5× the scheduled headway or median lateness ≥ 8 min; degraded = any unplanned alert, gap ≥ 1.6× or lateness ≥ 4 min.", root);
  }
  await draw();
  liveTimer = setInterval(draw, 60000);
}


// ---------------------------------------------------------------- plan (trip time planner)
const minTxt = s => s == null ? "–" : `${(s / 60).toFixed(0)} min`;
function renderPlanLive(box, board, schedule, feeds) {
  const plan = planJourneys(schedule, feeds, board.now), now = board.now;
  box.replaceChildren();
  const card = h("div", "card", null, box);
  const hd = h("div", "row between", null, card);
  h("h3", null, "Live from the MTA feeds: leave now", hd);
  h("span", "small secondary", `${board.demo ? "recorded snapshot replayed at" : "polled"} ${hhmmss(now)} ET · every 30 s`, hd);
  if (!plan.journeys.length) { h("div", "small secondary", "No journeys in data/client_schedule.json (rebuild the site).", card); return; }
  const wrap = h("div", "table-wrap", null, card); const t = h("table", "tiny", null, wrap); const tr = h("tr", null, null, h("thead", null, null, t));
  [["journey", ""], ["first train in", "num"], ["arrive", "num"], ["total", "num"], ["itinerary", ""], ["next option", "num hide-sm"]].forEach(([x, cl]) => h("th", cl, x, tr)); const tb = h("tbody", null, null, t);
  plan.journeys.forEach(j => { const row = h("tr", null, null, tb); const c0 = h("td", null, null, row); link(`#/plan/${j.id}`, j.label, c0, "small");
    const b = j.best;
    if (!b) { const td = h("td", "small secondary", "no complete itinerary in the feed within the hour", row); td.colSpan = 5; return; }
    h("td", "num", minTxt(b.legs[0].wait_sec), row); h("td", "num eta", hhmm(b.arrive_ts), row); h("td", "num", minTxt(b.total_sec), row);
    const it = h("td", "small", null, row);
    it.append(b.legs.map(l => `${l.route} ${(l.train_id || "").trim()}: board ${hhmm(l.board_ts)}${l.position ? ` (now ${posText(l.position)})` : l.started ? "" : " (not yet departed)"}, ride ${minTxt(l.ride_sec)} to ${l.to_name}${l.transfer_sec ? ` after a ${minTxt(l.transfer_sec)} walk` : ""}`).join(" → "));
    (b.warnings || []).forEach(x => { const c = h("span", "status-chip st-degraded", null, it); c.style.marginLeft = ".4rem"; h("span", "dot", null, c); c.append(x); });
    const nx = j.options[1]; h("td", "num small hide-sm", nx ? `${minTxt(nx.legs[0].wait_sec)} → ${hhmm(nx.arrive_ts)}` : "–", row); });
  h("div", "tiny muted", "Straight from the feed: the next train of the leg's routes at the origin, its own ETA at the leg's destination, the transfer walk, then the next train there. No model calibration; the snapshot below adds it. Trains holding or stalled right now are flagged.", card);
}

async function plan(idx, journeyId) {
  const root0 = app; root0.replaceChildren();
  const head = h("div", "row between", null, root0);
  h("h1", null, "Trip planner: how long will it take right now?", head);
  const ctl = h("div", "refresh small secondary", null, head);
  const liveBox = h("div", null, null, root0);
  const snapBox = h("div", null, null, root0);
  setupClientLive(ctl, liveBox, { feedKeys: journeyFeeds, render: renderPlanLive });
  async function draw() {
    const root = snapBox;
    let d;
    try { await dataReady; const r = await fetch(DATA + "live.json", { cache: "no-store" }); if (!r.ok) throw new Error(String(r.status)); d = await r.json(); }
    catch (e) { root.replaceChildren(); h("div", "empty", "No live snapshot yet. The planner needs data/live.json (published by the collector, or served by mta-insights serve); the live feeds toggle above works without it.", root); return; }
    const journeys = d.journeys || [];
    root.replaceChildren();
    const rf = h("div", "refresh small secondary", null, root); rf.style.justifyContent = "flex-end";
    h("span", "age", `model snapshot as of ${hhmm(d.generated_ts)} ET · ${ageText(Date.now() / 1000 - d.generated_ts)} · ${d.source}`, rf);
    const btn = h("button", "icon-btn", "↻", rf); btn.title = "Refresh"; btn.addEventListener("click", draw);
    if (!journeys.length) { h("div", "empty", "No journeys configured. Add them under \"journeys\" in pipeline/targets.json.", root); return; }
    // Route choice across alternatives for the same origin/destination
    const rc = (d.route_choice || []).filter(x => !x.error);
    if (rc.length) {
      h("h2", null, "Which way right now?", root);
      rc.forEach(g => { const card = h("div", "card", null, root);
        h("div", "row between", null, card).append(Object.assign(h("strong", null, `${g.origin} → ${g.destination}`), {}));
        h("p", "small", g.recommendation, card).style.marginTop = ".4rem";
        const wrap = h("div", "table-wrap", null, card); const t = h("table", null, null, wrap); const tr = h("tr", null, null, h("thead", null, null, t));
        [["option", ""], ["trains", ""], ["leave in", "num"], ["arrive", "num"], ["total", "num"], ["range", "num hide-sm"], ["vs best", "num"]].forEach(([x, c]) => h("th", c, x, tr)); const tb = h("tbody", null, null, t);
        g.alternatives.forEach((a, i) => { const r = h("tr", i === 0 ? "worse" : "", null, tb); const c0 = h("td", null, null, r); link(`#/plan/${a.id}`, a.label, c0, "small");
          const c1 = h("td", null, null, r); a.routes.forEach(x => routeBullet(x, c1)); h("td", "num", minTxt(a.depart_ts - d.generated_ts), r); h("td", "num eta", hhmm(a.arrive_ts), r); h("td", "num", minTxt(a.total_sec), r); h("td", "num small hide-sm", `±${(a.range_sec / 120).toFixed(0)} min`, r);
          h("td", "num", i === 0 ? "best" : `+${(a.vs_best_sec / 60).toFixed(0)} min`, r); if (a.tight_connection) { const w = h("span", "status-chip st-degraded", null, c0); h("span", "dot", null, w); w.append("tight connection"); } }); });
    }
    const filters = h("div", "filters", null, root);
    h("label", "small secondary", "Journey", filters);
    const sel = h("select", null, null, filters);
    journeys.forEach(j => { const o = h("option", null, j.label, sel); o.value = j.id; });
    sel.value = journeyId && journeys.some(j => j.id === journeyId) ? journeyId : journeys[0].id;
    sel.addEventListener("change", () => { location.hash = `#/plan/${sel.value}`; });
    const j = journeys.find(x => x.id === sel.value);
    if (j.error) { h("div", "empty", `Planner error: ${j.error}`, root); return; }
    const now = d.generated_ts, best = j.best;
    const tiles = h("div", "tiles", null, root);
    if (best) {
      tile(tiles, "Leave now: arrive", hhmm(best.arrive_ts), `${minTxt(best.total_sec)} door to door (${minTxt(best.total_lo_sec)}–${minTxt(best.total_hi_sec)})`);
      tile(tiles, "First train", `${best.legs[0].route_id} in ${minTxt(best.wait_sec)}`, best.legs[0].train_now_at ? `now at ${best.legs[0].train_now_at}` : "");
      tile(tiles, "Typical at this hour", minTxt(j.typical_total_sec), "schedule + typical waits");
      const delta = best.total_sec - j.typical_total_sec;
      tile(tiles, "Right now vs typical", `${delta >= 0 ? "+" : "−"}${Math.abs(delta / 60).toFixed(0)} min`, delta > 180 ? "slower than usual" : delta < -180 ? "faster than usual" : "about normal");
    } else {
      h("div", "empty", "No catchable train within the next hour appears in the feed for the first leg.", root);
    }
    // Options table
    if ((j.options || []).length) {
      h("h2", null, "Options in the next hour", root);
      const wrap = h("div", "table-wrap card", null, root); const t = h("table", null, null, wrap);
      const tr = h("tr", null, null, h("thead", null, null, t));
      [["leave in", ""], ["trains", ""], ["board", "num hide-sm"], ["arrive", "num"], ["total", "num"], ["range", "num hide-sm"], ["breakdown", ""]].forEach(([x, c]) => h("th", c, x, tr));
      const tb = h("tbody", null, null, t);
      j.options.forEach((o, i) => { const row = h("tr", i === 0 ? "worse" : "", null, tb);
        h("td", null, minTxt(o.wait_sec), row); const rc = h("td", null, null, row); o.routes.forEach(r => routeBullet(r, rc));
        h("td", "num eta hide-sm", hhmm(o.depart_ts), row); h("td", "num eta", hhmm(o.arrive_ts), row); h("td", "num", minTxt(o.total_sec), row);
        h("td", "num small hide-sm", `${minTxt(o.total_lo_sec)}–${minTxt(o.total_hi_sec)}`, row);
        const bd = h("td", "small", null, row);
        bd.append(o.legs.map(l => `${l.route_id}: wait ${minTxt(l.wait_sec)}${l.transfer_sec ? ` + walk ${minTxt(l.transfer_sec)}` : ""} + ride ${minTxt(l.ride_sec)}${l.ride_source === "typical" ? " (typical)" : l.ride_source === "model" ? " (schedule+model)" : ""}${l.train_lateness_sec != null && Math.abs(l.train_lateness_sec) >= 120 ? ` (train ${lateTxt(l.train_lateness_sec)})` : ""}`).join(" → "));
        o.legs.filter(l => l.connection_risk).forEach(l => { const c = h("span", `status-chip st-${l.connection_risk === "tight" ? "degraded" : "good"}`, null, bd); c.style.marginLeft = ".4rem"; h("span", "dot", null, c);
          c.append(l.connection_risk === "tight" ? `tight connection at ${l.from_name}: ${(l.connection_margin_sec / 60).toFixed(1)} min margin${l.next_if_missed_sec != null ? `, next ${l.route_id} in ${minTxt(l.next_if_missed_sec)} if missed` : ""}` : `connection at ${l.from_name}: ${minTxt(l.connection_margin_sec)} margin`); });
        (o.warnings || []).forEach(w => { const c = h("span", "status-chip st-disrupted", null, bd); c.style.marginLeft = ".4rem"; h("span", "dot", null, c); c.append(w); }); });
    }
    // Stringline chart with the recommended itinerary drawn
    if ((j.stringline || []).length) {
      h("h2", null, "Trains on this corridor right now", root);
      const card = h("div", "card", null, root);
      const hl = new Set((best?.legs || []).map(l => l.trip_id).filter(Boolean));
      const path = [];
      (best?.legs || []).forEach((l, li) => { const lg = j.stringline[li]; if (!lg) return; const iFrom = lg.stops.findIndex(s => s.stop_id === l.from), iTo = lg.stops.findIndex(s => s.stop_id === l.to);
        if (li === 0) path.push([0, Math.max(0, iFrom), now]); path.push([li, Math.max(0, iFrom), l.board_ts]); path.push([li, Math.max(0, iTo), l.arrive_ts]); });
      stringline(card, { title: "Time-distance view", subtitle: "each line is a train (feed ETAs); the dashed red path is the recommended itinerary: wait, ride, transfer, ride", legs: j.stringline, now, highlight: hl, path, routeColor: r => ROUTE_COLORS[r] || null });
      const lg = h("div", "small secondary", null, card); lg.style.marginTop = ".4rem";
      lg.textContent = "Highlighted lines are the trains you would take; grey lines are other trains on the corridor. Steeper lines mean faster running; flat segments are dwells or holds.";
    }
    // Leave-by calculator: arrive by a chosen time with 90% confidence
    const lb = (d.leave_by || []).find(x => x.id === j.id);
    if (lb && lb.hours.length) {
      h("h2", null, "When should I leave?", root);
      const card = h("div", "card", null, root);
      h("div", "small secondary", "Budget for arriving by a given time with 90% confidence: p90 waits at this hour, scheduled rides plus the model's p90 excess, and the transfer walks. Typical is the expected trip.", card);
      const wrap = h("div", "table-wrap", null, card); const t = h("table", null, null, wrap); const tr = h("tr", null, null, h("thead", null, null, t));
      ["arrive by", "be on the platform by", "budget (90%)", "typical trip"].forEach((x, i) => h("th", i ? "num" : "", x, tr)); const tb = h("tbody", null, null, t);
      lb.hours.forEach(x => { const r = h("tr", null, null, tb); h("td", null, hhmm(x.arrive_by_ts), r); h("td", "num eta", hhmm(x.leave_by_ts), r); h("td", "num", minTxt(x.conservative_total_sec), r); h("td", "num", minTxt(x.typical_total_sec), r); });
    }
    // Cross-line context from the route analysis
    try {
      const ra = await load("routes.json"); const rr = (ra.routes || []).find(x => x.id === j.id);
      if (rr && rr.findings && rr.findings.length) {
        h("h2", null, "What history says about this route", root);
        const card = h("div", "card", null, root); const ul = h("ul", "findings", null, card);
        rr.findings.slice(0, 4).forEach(f => { const li = h("li", `sev-${f.severity}`, null, ul); h("span", "sev", f.severity, li); li.append(" " + f.text); });
        link(`#/routes/${j.id}`, "Full route analysis: transfers, cross-line effects, where the time goes →", card, "small");
      }
    } catch (e) { /* routes.json is optional */ }
    // Model explanation
    const m = j.model || {};
    const det = h("details", null, null, root); det.style.marginTop = "1rem"; h("summary", null, "How the estimate is built", det);
    const ul = h("ul", "small secondary", null, det);
    h("li", null, `Ride time per leg: average of the feed's own ETA difference (when the train's ETA at the destination is published) and the schedule plus the model's predicted excess.`, ul);
    h("li", null, `Model: ridge regression on the collected history (${m.n_samples || 0} observed rides${m.fitted_at ? `, fitted ${dateTime(m.fitted_at)}` : ""}); coefficients shrink to zero until enough rides are observed.`, ul);
    Object.entries(m.coef || {}).forEach(([li, coef]) => { const parts = Object.entries(coef).filter(([, v]) => Math.abs(v) >= 5).map(([k, v]) => `${k.replace(/_/g, " ")}: ${v >= 0 ? "+" : "−"}${Math.abs(v).toFixed(0)} s`); h("li", null, `Leg ${Number(li) + 1} effects: ${parts.length ? parts.join(", ") : "none learned yet"}`, ul); });
    h("li", null, "Range: p10–p90 of the model's residuals for the route and period (weekday peak, off-peak, weekend), summed over legs.", ul);
    h("li", null, "Signals used: lateness of the train at boarding, unplanned alerts on the route, holidays, weekends, peak, permitted street events and venue events, transit news mentions, precipitation and heat.", ul);
  }
  await draw();
  liveTimer = setInterval(draw, 60000);
}


// ---------------------------------------------------------------- routes (cross-line effects, full route analysis)
const HOURS = Array.from({ length: 24 }, (_, i) => `${i}`);
async function routesPage(idx, routeId) {
  const root = app; root.replaceChildren();
  let ra;
  try { ra = await load("routes.json"); } catch (e) { h("h1", null, "Route analysis", root); h("div", "empty", "No route analysis yet (data/routes.json missing).", root); return; }
  const list = ra.routes || [];
  h("h1", null, "Route analysis: transfers and cross-line effects", root);
  if (!list.length) { h("div", "empty", "No journeys configured, or no arrivals collected yet for their stops.", root); return; }
  const filters = h("div", "filters", null, root);
  h("label", "small secondary", "Route", filters);
  const sel = h("select", null, null, filters);
  list.forEach(r => { const o = h("option", null, r.label, sel); o.value = r.id; });
  sel.value = routeId && list.some(r => r.id === routeId) ? routeId : list[0].id;
  sel.addEventListener("change", () => { location.hash = `#/routes/${sel.value}`; });
  const r = list.find(x => x.id === sel.value);
  const d = r.decomposition || {};
  h("div", "small secondary", `History ${ra.start_ts ? new Date(ra.start_ts * 1000).toLocaleDateString() : ""} – ${ra.end_ts ? new Date(ra.end_ts * 1000).toLocaleDateString() : ""}; observed rides per leg: ${Object.values(d.n_rides || {}).join(" / ") || "0"}`, root);
  if (r.status !== "ok") h("div", "empty", "Collecting: fewer than 20 observed rides on a leg. Findings appear once the corridor stops have a few days of arrivals.", root);
  // Tiles
  const tiles = h("div", "tiles", null, root);
  const tot = (d.total_by_hour || []).filter(v => v != null);
  const worstH = tot.length ? (d.total_by_hour || []).indexOf(Math.max(...tot)) : null;
  tile(tiles, "Mean excess over schedule", tot.length ? minTxt(tot.reduce((a, b) => a + b, 0) / tot.length) : "–", worstH != null ? `worst hour ${String(worstH).padStart(2, "0")}:00 (${minTxt(d.total_by_hour[worstH])})` : "");
  tile(tiles, "Largest component", d.dominant ? d.dominant.replace(/^(wait|ride|transfer)/, m => m) : "–", d.dominant && d.shares && d.shares[d.dominant] != null ? `${(d.shares[d.dominant] * 100).toFixed(0)}% of the excess` : "");
  tile(tiles, "Share from transfers", d.cross_line_share != null ? `${(d.cross_line_share * 100).toFixed(0)}%` : "–", "extra connection wait vs schedule");
  const trs = (ra.transfers || []).filter(t => (r.transfers || []).includes(t.id));
  const mr = trs.map(t => t.summary && t.summary.missed_rate).filter(v => v != null);
  tile(tiles, "Missed connections", mr.length ? `${(Math.max(...mr) * 100).toFixed(0)}%` : "–", trs.length ? `at ${trs.map(t => t.station_name).join(", ")}` : "no transfer on this route");
  // Findings
  if ((r.findings || []).length) {
    h("h2", null, "Findings", root);
    const card = h("div", "card", null, root); const ul = h("ul", "findings", null, card);
    r.findings.forEach(f => { const li = h("li", `sev-${f.severity}`, null, ul); h("span", "sev", f.severity, li); li.append(" " + f.text); });
  }
  // Decomposition chart
  const comps = (d.components || []).filter(c => c.by_hour && c.by_hour.some(v => v != null));
  if (comps.length) {
    h("h2", null, "Where the time goes, by hour", root);
    const card = h("div", "card", null, root);
    barChart(card, { title: "Mean excess over schedule by component", subtitle: "minutes above the scheduled wait, ride or connection, averaged over the history; negative values (faster than schedule) are shown as zero",
      categories: HOURS, series: comps.map((c, i) => ({ name: c.name, values: c.by_hour.map(v => v == null ? 0 : Math.max(0, v)), color: c.kind === "transfer" ? cssVarJs("--series-8") : undefined })),
      stacked: true, format: fmt.min, labelEvery: 3, height: 260 });
  }
  // Transfers
  trs.forEach(t => {
    const s = t.summary || {};
    h("h2", null, `Transfer at ${t.station_name}: ${t.from_routes.join("/")} → ${t.to_routes.join("/")} (walk ${(t.walk_sec / 60).toFixed(0)} min)`, root);
    if (!s.ok) { h("div", "empty", `Collecting: ${s.n || 0} connections observed (need 20).`, root); return; }
    const card = h("div", "card", null, root);
    const tl = h("div", "tiles", null, card);
    tile(tl, "Connection wait", minTxt(s.wait_median_sec), `median; schedule ${minTxt(s.sched_wait_median_sec)}; p90 ${minTxt(s.wait_p90_sec)}`);
    tile(tl, "Missed the planned train", s.missed_rate != null ? `${(s.missed_rate * 100).toFixed(0)}%` : "–", `of ${s.n_planned_observed} connections`);
    const eff = s.feeder_lateness_effect || {};
    tile(tl, `Cost of a late ${t.from_routes.join("/")}`, eff.excess_wait_diff_sec != null ? `${eff.excess_wait_diff_sec >= 0 ? "+" : "−"}${Math.abs(eff.excess_wait_diff_sec / 60).toFixed(1)} min` : "–", eff.missed_rate_late != null ? `missed ${(eff.missed_rate_late * 100).toFixed(0)}% vs ${(eff.missed_rate_on_time * 100).toFixed(0)}% on time` : "");
    const c = t.comovement || {};
    tile(tl, "Lines move together?", c.spearman != null ? `ρ ${c.spearman >= 0 ? "+" : "−"}${Math.abs(c.spearman).toFixed(2)}` : "–", c.joint_lift != null ? `joint disruption ${c.joint_lift.toFixed(1)}× chance` : (c.n_bins ? `${c.n_bins} bins` : "collecting"));
    const bh = s.by_hour || [];
    if (bh.some(x => x.wait_median_sec != null)) {
      lineChart(card, { title: "Connection wait by hour", subtitle: "median and p90 observed vs the scheduled connection", x: HOURS,
        series: [{ name: "median", values: bh.map(x => x.wait_median_sec) }, { name: "p90", values: bh.map(x => x.wait_p90_sec) }, { name: "scheduled", values: bh.map(x => x.sched_wait_median_sec), color: cssVarJs("--text-secondary") }],
        format: fmt.min, labelEvery: 3, yMin: 0 });
    }
    const wrap = h("div", "table-wrap", null, card); const tb = h("table", null, null, wrap);
    const hr = h("tr", null, null, h("thead", null, null, tb)); ["feeder arrival", "n", "median wait", "extra wait vs schedule", "missed"].forEach((x, i) => h("th", i ? "num" : "", x, hr));
    const body = h("tbody", null, null, tb);
    (s.by_feeder_lateness || []).forEach(b => { const row = h("tr", null, null, body); h("td", null, { on_time: "on time (<2 min late)", late_2_5: "2–5 min late", late_5_plus: "5+ min late" }[b.bucket] || b.bucket, row);
      h("td", "num", String(b.n), row); h("td", "num", minTxt(b.wait_median_sec), row); h("td", "num", b.excess_wait_mean_sec == null ? "–" : `${b.excess_wait_mean_sec >= 0 ? "+" : "−"}${Math.abs(b.excess_wait_mean_sec).toFixed(0)} s`, row); h("td", "num", b.missed_rate == null ? "–" : `${(b.missed_rate * 100).toFixed(0)}%`, row); });
  });
  // Interactions
  const ints = (r.interactions || []);
  if (ints.length) {
    h("h2", null, "Shared-track interaction between lines", root);
    const card = h("div", "card", null, root);
    h("div", "small secondary", "Time a train loses at a shared stop when another line's train is just ahead of where it would have arrived (within 3 min), vs free-running trains of the same line.", card);
    const wrap = h("div", "table-wrap", null, card); const tb = h("table", null, null, wrap);
    const hr = h("tr", null, null, h("thead", null, null, tb)); ["line", "behind a", "where", "when", "trips affected", "time lost", "95% CI", "p", "if leader late"].forEach((x, i) => h("th", i >= 4 ? "num" : "", x, hr));
    const body = h("tbody", null, null, tb);
    ints.forEach(x => { const b = x.best || x.per_stop[0]; if (!b) return; const row = h("tr", x.significant ? "worse" : "", null, body);
      routeBullet(x.route, h("td", null, null, row)); routeBullet(x.leader_route, h("td", null, null, row)); h("td", null, b.stop_name, row); h("td", null, b.scope && b.scope !== "all" ? b.scope.replace("_", " ") : "all day", row);
      h("td", "num", `${(b.conflict_rate * 100).toFixed(0)}%`, row); h("td", "num", `${b.extra_sec.toFixed(0)} s`, row); h("td", "num small", `${b.ci_lo.toFixed(0)}–${b.ci_hi.toFixed(0)}`, row); h("td", "num small", b.p_value < 0.001 ? "<0.001" : b.p_value.toFixed(3), row);
      h("td", "num", b.extra_when_leader_late_sec == null ? "–" : `${b.extra_when_leader_late_sec.toFixed(0)} s`, row); });
  }
  const det = h("details", null, null, root); det.style.marginTop = "1rem"; h("summary", null, "How to read this", det);
  const ul = h("ul", "small secondary", null, det);
  h("li", null, "Connection wait: from stepping off the feeder train plus the walk to the next connecting train's arrival. 'Missed' means the connecting train the schedule would have given (given the feeder's scheduled arrival) had already left.", ul);
  h("li", null, "Cost of a late feeder: extra connection wait for feeder trains ≥3 min late vs on-time ones (bootstrap CI, Mann-Whitney p). The lateness itself is on top of this.", ul);
  h("li", null, "Lines move together: Spearman correlation of the two lines' mean lateness in 15-minute bins at the station; the lag with the strongest correlation says which line leads. Joint disruption lift: how much more often both lines are ≥4 min late in the same bin than if independent.", ul);
  h("li", null, "Shared-track interaction: for each stop both lines serve, a train's lateness change from the previous stop when the other line's train arrived within 3 min before its projected arrival, vs when not. Reported per period when the effect is concentrated in a peak.", ul);
  h("li", null, "Where the time goes: mean excess over schedule per component (origin wait from observed vs scheduled headways, each ride, each transfer) by hour; shares use the positive components.", ul);
}
const fmtSec = v => v == null ? "–" : `${(v / 60).toFixed(1)} min`;
const cssVarJs = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();


// ---------------------------------------------------------------- model card
const secTxt = v => v == null ? "–" : `${v.toFixed(0)} s`;
async function modelPage(idx) {
  const root = app; root.replaceChildren();
  h("h1", null, "Arrival model: how good are the predictions?", root);
  let card;
  try { card = await load("models/arrival.card.json"); } catch (e) { h("div", "empty", "No model card yet (data/models/arrival.card.json).", root); return; }
  if (card.status !== "ok") { h("div", "empty", `Model not trained: ${card.status}${card.error ? ` (${card.error})` : ""}. Rows available: ${card.n_rows ?? "?"}; needed: ${card.min_rows ?? 500}. The hourly collection and the subwaydata.nyc backfill grow the training set.`, root); return; }
  const ev = card.evaluation || {};
  h("p", "secondary", "Gradient-boosted quantile models predict how much a train's lateness will change between the stop it just served and a stop 1–12 stops ahead, from its state, the traffic ahead, the segment's last few trains, the feed's own forecast, alerts, weather and events. Evaluated on the most recent 20% of the history, never seen in training.", root);
  const tiles = h("div", "tiles", null, root);
  tile(tiles, "Model error (MAE)", secTxt(ev.mae_model), `schedule ${secTxt(ev.mae_schedule)} · persistence ${secTxt(ev.mae_persistence)}`);
  tile(tiles, "vs the MTA countdown ETA", ev.mae_feed != null ? `${((1 - ev.mae_model_on_feed_rows / ev.mae_feed) * 100).toFixed(0)}% better` : "collecting", ev.mae_feed != null ? `feed ${secTxt(ev.mae_feed)} vs model ${secTxt(ev.mae_model_on_feed_rows)} on ${fmt.compact(ev.n_with_feed)} sampled rows` : "needs ETA samples at the monitored stops");
  tile(tiles, "80% range coverage", ev.coverage_p10_p90 != null ? `${(ev.coverage_p10_p90 * 100).toFixed(0)}%` : "–", `median band width ${secTxt(ev.range_width_median_sec)}${ev.range_scale ? ` · conformal scale ${ev.range_scale.toFixed(2)}` : ""}`);
  tile(tiles, "Training set", fmt.compact(card.n_train), `${fmt.compact(card.n_test)} held out · ${card.routes_seen ? card.routes_seen.length : "?"} routes · fitted ${card.trained_at ? dateTime(new Date(card.trained_at * 1000).toISOString()) : ""}`);
  if ((ev.by_k || []).length) {
    h("h2", null, "Error by horizon (stops ahead)", root);
    const card1 = h("div", "card", null, root);
    barChart(card1, { title: "Mean absolute error of the predicted lateness change", subtitle: "lower is better; the schedule baseline assumes no change, persistence repeats the segment's recent excess",
      categories: ev.by_k.map(b => `${b.k} stop${b.k > 1 ? "s" : ""}`),
      series: [{ name: "model", values: ev.by_k.map(b => b.mae_model) }, { name: "schedule", values: ev.by_k.map(b => b.mae_schedule) }, { name: "persistence", values: ev.by_k.map(b => b.mae_persistence) },
               ...(ev.by_k.some(b => b.mae_feed != null) ? [{ name: "MTA feed ETA", values: ev.by_k.map(b => b.mae_feed || 0) }] : [])],
      format: fmt.sec, height: 240 });
    const wrap = h("div", "table-wrap", null, card1); const t = h("table", null, null, wrap); const tr = h("tr", null, null, h("thead", null, null, t));
    ["horizon", "n", "model", "schedule", "persistence", "feed", "coverage"].forEach((x, i) => h("th", i ? "num" : "", x, tr)); const tb = h("tbody", null, null, t);
    ev.by_k.forEach(b => { const r = h("tr", null, null, tb); h("td", null, `${b.k} stops`, r); h("td", "num", fmt.compact(b.n), r); h("td", "num", secTxt(b.mae_model), r); h("td", "num", secTxt(b.mae_schedule), r); h("td", "num", secTxt(b.mae_persistence), r); h("td", "num", b.mae_feed == null ? "–" : secTxt(b.mae_feed), r); h("td", "num", `${(b.coverage * 100).toFixed(0)}%`, r); });
  }
  if ((ev.by_route || []).length) {
    h("h2", null, "Error by route", root);
    const c2 = h("div", "card", null, root);
    barChart(c2, { title: "MAE by route: model vs schedule", categories: ev.by_route.map(b => b.route), series: [{ name: "model", values: ev.by_route.map(b => b.mae_model) }, { name: "schedule", values: ev.by_route.map(b => b.mae_schedule) }], format: fmt.sec, height: 220 });
  }
  if ((card.importance || []).length) {
    h("h2", null, "What the model relies on", root);
    const c3 = h("div", "card", null, root);
    const imp = card.importance.filter(i => i.mae_increase > 0).slice(0, 12);
    const short = { seg_recent_excess: "segment now", dest_recent_lateness: "dest. now", sched_headway_sec: "sched hw", gap_ahead_sec: "gap ahead", leader_lateness: "leader late", leader_same_route: "leader route", lateness_u: "lateness", sched_run_sec: "sched run", feed_excess: "feed ETA", track_changed: "track", hour_sin: "hour (sin)", hour_cos: "hour (cos)", route_code: "route", direction_code: "direction", cause_code: "cause", alert_active: "alert", planned_active: "planned", precip_mm: "rain", venue_event_w: "venue", street_event_w: "street", news_w: "news" };
    barChart(c3, { title: "Permutation importance", subtitle: "increase in error when the feature is shuffled (seconds); full names in the table view", categories: imp.map(i => short[i.feature] || i.feature.replace(/_/g, " ")), series: [{ name: "MAE increase", values: imp.map(i => i.mae_increase) }], format: fmt.sec, height: 240, labelEvery: 1 });
    if ((card.dropped_features || []).length) h("div", "small secondary", `Not usable yet (constant or missing in the training data): ${card.dropped_features.join(", ")}.`, c3);
  }
  if ((ev.calibration_by_width || []).length) {
    h("h2", null, "Does a wide range mean real uncertainty?", root);
    const c4 = h("div", "card", null, root);
    const wrap = h("div", "table-wrap", null, c4); const t = h("table", null, null, wrap); const tr = h("tr", null, null, h("thead", null, null, t));
    ["range quartile", "n", "median band", "actual error (MAE)"].forEach((x, i) => h("th", i ? "num" : "", x, tr)); const tb = h("tbody", null, null, t);
    ev.calibration_by_width.forEach(b => { const r = h("tr", null, null, tb); h("td", null, ["narrowest", "narrow", "wide", "widest"][b.bucket] || String(b.bucket), r); h("td", "num", fmt.compact(b.n), r); h("td", "num", secTxt(b.width_median), r); h("td", "num", secTxt(b.mae), r); });
    h("div", "small secondary", "Error should rise with the band width: then the range is informative, not just noise.", c4);
  }
  try {
    const fe = await load("forecast_eval.json");
    if (fe && fe.n) {
      h("h2", null, "Live forecasts scored against what happened", root);
      const c5 = h("div", "card", null, root);
      h("p", "small secondary", `${fmt.compact(fe.n)} predicted arrivals at the monitored platforms from ${fmt.compact(fe.n_snapshots)} live snapshots over ${fe.days} day${fe.days === 1 ? "" : "s"}, each matched to the arrival observed afterwards. Error = prediction − actual; a negative bias means trains arrived later than predicted.`, c5);
      const o = fe.overall || {};
      const tiles = h("div", "tiles", null, c5);
      tile(tiles, "MTA feed ETA", secTxt(o.feed && o.feed.mae_sec), o.feed && o.feed.bias_sec != null ? `bias ${o.feed.bias_sec > 0 ? "+" : ""}${o.feed.bias_sec.toFixed(0)} s · p90 ${secTxt(o.feed.p90_abs_sec)}` : "");
      tile(tiles, "Model ETA (Live page)", secTxt(o.model && o.model.mae_sec), o.model_beats_feed_share != null ? `closer than the feed ${(o.model_beats_feed_share * 100).toFixed(0)}% of the time` : "");
      tile(tiles, "Forward simulation", secTxt(o.sim && o.sim.mae_sec), o.sim_beats_feed_share != null ? `closer than the feed ${(o.sim_beats_feed_share * 100).toFixed(0)}% of the time · n ${fmt.compact(o.sim && o.sim.n)}` : "");
      if ((fe.by_horizon || []).length) {
        const lab = b => b.horizon_hi_sec ? `${b.horizon_lo_sec / 60}–${b.horizon_hi_sec / 60} min` : `${b.horizon_lo_sec / 60}+ min`;
        barChart(c5, { title: "MAE by forecast horizon", subtitle: "how far ahead the arrival was predicted", categories: fe.by_horizon.map(lab),
          series: [{ name: "feed", values: fe.by_horizon.map(b => b.feed.mae_sec || 0) }, { name: "model", values: fe.by_horizon.map(b => b.model.mae_sec || 0) }, { name: "simulation", values: fe.by_horizon.map(b => b.sim.mae_sec || 0) }], format: fmt.sec, height: 220 });
      }
      if ((fe.corroboration || []).length) {
        const wrap = h("div", "table-wrap", null, c5); const t = h("table", null, null, wrap); const tr = h("tr", null, null, h("thead", null, null, t));
        ["position says", "n", "feed bias", "feed MAE", "model MAE", "simulation MAE", "arrived >1 min after the feed's ETA"].forEach((x, i) => h("th", i ? "num" : "", x, tr)); const tb = h("tbody", null, null, t);
        fe.corroboration.forEach(r => { const row = h("tr", null, null, tb); h("td", null, r.corroboration.replace(/_/g, " "), row); h("td", "num", fmt.compact(r.n), row); h("td", "num", r.feed_bias_sec == null ? "–" : `${r.feed_bias_sec > 0 ? "+" : ""}${r.feed_bias_sec.toFixed(0)} s`, row); h("td", "num", secTxt(r.feed_mae_sec), row); h("td", "num", secTxt(r.model_mae_sec), row); h("td", "num", secTxt(r.sim_mae_sec), row); h("td", "num", `${(r.share_arrived_later_than_feed * 100).toFixed(0)}%`, row); });
        h("div", "small secondary", "The corroboration check: when a train's reported position said the feed was optimistic, the feed's error should be strongly negative (the train arrived later than promised) and the position-corrected model and simulation should do better.", c5);
      }
      if (fe.held && fe.held.n) h("div", "small secondary", `Trains that were holding or stalled when predicted (${fe.held.n}): feed MAE ${secTxt(fe.held.feed.mae_sec)}, simulation baseline ${secTxt(fe.held.sim.mae_sec)}${fe.held.hold_persists && fe.held.hold_persists.n ? `, "hold persists" scenario ${secTxt(fe.held.hold_persists.mae_sec)}` : ""}.`, c5);
    }
  } catch (e) { /* optional */ }
  const det = h("details", null, null, root); det.style.marginTop = "1rem"; h("summary", null, "How it is used", det);
  const ul = h("ul", "small secondary", null, det);
  h("li", null, "Live page: each upcoming train's ETA and range come from this model when it is ready (source 'learned'); otherwise from the look-back calibration of the feed.", ul);
  h("li", null, "Trip planner: ride times and the arrival at the boarding stop use the model for trains already under way; the feed's ETA is a feature when sampled, or blended in by inverse variance when not.", ul);
  h("li", null, "The training table grows with every hourly run (all stops of every feed) and with the subwaydata.nyc backfill of recent days; the model is refitted at every site build on a strict time split.", ul);
}


// ---------------------------------------------------------------- disruption climatology (historical alerts archive)
const DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
async function holdsSection(root) {
  try {
    const hs = await load("holds.json");
    if (hs && hs.n) {
      h("h2", null, "Where trains get held, from the vehicle positions", root);
      const ch = h("div", "card", null, root);
      h("p", "small secondary", `${fmt.compact(hs.n)} holds (a train reported stopped at a station for ≥ ${(hs.hold_sec / 60).toFixed(1)} min) at every stop of the polled feeds over ${hs.days} day${hs.days === 1 ? "" : "s"}, origin terminals excluded${hs.n_terminal ? ` (${fmt.compact(hs.n_terminal)} terminal waits left out)` : ""}: ${hs.per_day} per day, median ${(hs.median_sec / 60).toFixed(1)} min. Holds are the first visible symptom of most incidents.`, ch);
      const tiles = h("div", "tiles", null, ch);
      tile(tiles, "Holds per day", hs.per_day, `median ${(hs.median_sec / 60).toFixed(1)} min`);
      if (hs.long) {
        tile(tiles, `Long holds (≥ ${hs.long_sec / 60} min)`, hs.long.per_day + " / day", `median ${(hs.long.median_hold_sec / 60).toFixed(0)} min`);
        tile(tiles, "Long holds with an alert", `${(hs.long.share_with_alert * 100).toFixed(0)}%`, `${(hs.long.share_alert_after * 100).toFixed(0)}% alerted after the hold began · ${(hs.long.share_alert_before * 100).toFixed(0)}% already alerted`);
        tile(tiles, "Alert latency", hs.long.median_latency_sec != null ? `${(hs.long.median_latency_sec / 60).toFixed(0)} min` : "–", hs.long.median_latency_sec != null ? `median from the hold's start to the alert · p75 ${(hs.long.p75_latency_sec / 60).toFixed(0)} min` : "no alert followed a long hold yet");
      }
      const two = h("div", "two", null, ch);
      barChart(h("div", null, null, two), { title: "Holds per day by hour", categories: HOURS, series: [{ name: "holds/day", values: hs.by_hour }], format: fmt.num1, labelEvery: 3, height: 200 });
      const br = hs.by_route.slice(0, 20);
      barChart(h("div", null, null, two), { title: "Minutes held per day by line", categories: br.map(r => r.route), series: [{ name: "min/day", values: br.map(r => r.total_min_per_day) }], format: fmt.num1, height: 200 });
      const wrapH = h("div", "table-wrap", null, ch); const th = h("table", null, null, wrapH); const trh = h("tr", null, null, h("thead", null, null, th));
      ["stop", "lines", "holds/day", "median", "p90", "minutes held", "worst hours"].forEach((x, i) => h("th", i >= 2 && i <= 5 ? "num" : "", x, trh)); const tbh = h("tbody", null, null, th);
      hs.by_stop.slice(0, 15).forEach(x => { const r = h("tr", null, null, tbh); h("td", null, `${x.name}`, r); const rc = h("td", null, null, r); x.routes.forEach(q => routeBullet(q, rc)); h("td", "num", fmt.num1(x.per_day), r); h("td", "num", `${(x.median_sec / 60).toFixed(1)} min`, r); h("td", "num", `${(x.p90_sec / 60).toFixed(1)} min`, r); h("td", "num", fmt.num1(x.total_min), r); h("td", "small", x.worst_hours.map(hh => `${String(hh).padStart(2, "0")}:00`).join(", "), r); });
      if ((hs.longest || []).length) {
        const det = h("details", null, null, ch); h("summary", "small", "Longest holds and whether an alert followed", det);
        const ul = h("ul", "small secondary", null, det);
        hs.longest.forEach(x => h("li", null, `${x.route} at ${x.name}, ${dateTime(new Date(x.start_ts * 1000).toISOString())}: held ${(x.dwell_sec / 60).toFixed(0)} min · ${x.alert_latency_sec == null ? "no unplanned alert for the line" : x.alert_latency_sec > 0 ? `alert ${(x.alert_latency_sec / 60).toFixed(0)} min after the hold began` : `alert already posted ${(-x.alert_latency_sec / 60).toFixed(0)} min earlier`}`, ul));
      }
      h("div", "small secondary", "Terminals and relay points hold trains by design (schedule recovery, crew changes); a mid-line station with frequent long holds is a signal, merge or dispatching problem. The alert latency is how long riders on the platform knew before the MTA said so.", ch);
    }
  } catch (e) { /* optional */ }
}

async function disruptionsPage(idx, routeSel) {
  const root = app; root.replaceChildren();
  h("h1", null, "Disruption climatology: when and where the subway breaks", root);
  let c;
  try { c = await load("climatology.json"); } catch (e) { h("div", "empty", "No climatology yet (data/climatology.json).", root); await holdsSection(root); return; }
  if (!c.n_events) { h("div", "empty", "The historical alerts archive (data.ny.gov, since 2020) has not been pulled yet; the hourly context step fetches it.", root); await holdsSection(root); return; }
  h("p", "secondary", `${fmt.compact(c.n_events)} unplanned disruption events (delays, suspensions, reroutes, skipped stops, slow speeds) from the MTA service-alert archive over ${c.weeks.toFixed(0)} weeks (${new Date(c.first_ts * 1000).toLocaleDateString()} – ${new Date(c.last_ts * 1000).toLocaleDateString()}). Each event is one alert thread; its duration is the time from the first to the last update.`, root);
  const tiles = h("div", "tiles", null, root);
  const perWeek = c.n_events / c.weeks;
  tile(tiles, "Disruption events", `${perWeek.toFixed(0)} / week`, `${(perWeek / 7).toFixed(1)} per day across the system`);
  const top = c.by_route[0];
  tile(tiles, "Most disrupted line", top ? top.route : "–", top ? `${top.per_week.toFixed(1)} events/week, median ${top.median_duration_min.toFixed(0)} min` : "");
  const worstH = c.per_week_by_hour.indexOf(Math.max(...c.per_week_by_hour));
  tile(tiles, "Worst hour", `${String(worstH).padStart(2, "0")}:00`, `${c.per_week_by_hour[worstH].toFixed(1)} events/week start then`);
  const bc = c.by_cause[0];
  tile(tiles, "Top cause", bc ? causeName(bc.cause) : "–", bc ? `${(bc.share * 100).toFixed(0)}% of events, median ${bc.median_duration_min.toFixed(0)} min` : "");
  h("h2", null, "Events per week by line", root);
  const c1 = h("div", "card", null, root);
  const br = c.by_route.slice(0, 26);
  barChart(c1, { title: "Unplanned disruption events per week", subtitle: "a line appears in every event that names it", categories: br.map(r => r.route), series: [{ name: "events/week", values: br.map(r => r.per_week) }], format: fmt.num1, height: 240, labelEvery: 1 });
  h("h2", null, "When disruptions start", root);
  const c2 = h("div", "card", null, root); const two = h("div", "two", null, c2);
  barChart(h("div", null, null, two), { title: "By hour of day", categories: HOURS, series: [{ name: "events/week", values: c.per_week_by_hour }], format: fmt.num1, labelEvery: 3, height: 200 });
  barChart(h("div", null, null, two), { title: "By day of week", categories: DOW, series: [{ name: "events/week", values: c.per_week_by_dow }], format: fmt.num1, height: 200 });
  const routesAvail = Object.keys(c.grid_by_route || {}).sort();
  if (routesAvail.length) {
    h("h2", null, "Line heatmap: day × hour", root);
    const c3 = h("div", "card", null, root);
    const filters = h("div", "filters", null, c3); h("label", "small secondary", "Line", filters);
    const sel = h("select", null, null, filters); routesAvail.forEach(r => { const o = h("option", null, r, sel); o.value = r; });
    sel.value = routeSel && routesAvail.includes(routeSel) ? routeSel : (top && routesAvail.includes(top.route) ? top.route : routesAvail[0]);
    sel.addEventListener("change", () => { location.hash = `#/disruptions/${sel.value}`; });
    const g = c.grid_by_route[sel.value];
    heatmap(c3, { title: `${sel.value}: disruption events per week starting in each hour`, rows: DOW, cols: HOURS, values: g, format: fmt.num1, colLabelEvery: 3 });
  }
  await holdsSection(root);
  h("h2", null, "Causes and how long they last", root);
  const c4 = h("div", "card", null, root); const wrap = h("div", "table-wrap", null, c4); const t = h("table", null, null, wrap);
  const tr = h("tr", null, null, h("thead", null, null, t)); ["cause", "events", "share", "median duration", "p90 duration"].forEach((x, i) => h("th", i ? "num" : "", x, tr));
  const tb = h("tbody", null, null, t);
  c.by_cause.forEach(x => { const r = h("tr", null, null, tb); h("td", null, causeName(x.cause), r); h("td", "num", fmt.compact(x.n), r); h("td", "num", `${(x.share * 100).toFixed(0)}%`, r); h("td", "num", `${x.median_duration_min.toFixed(0)} min`, r); h("td", "num", `${x.p90_duration_min.toFixed(0)} min`, r); });
  const det = h("details", null, null, root); det.style.marginTop = "1rem"; h("summary", null, "How to use this", det);
  const ul = h("ul", "small secondary", null, det);
  h("li", null, "Base rates: the chance a new disruption starts on your line in the next hour is the heatmap cell for the current day and hour (events per week ÷ 1 week = expected events that hour of a typical week).", ul);
  h("li", null, "Durations are alert-thread lengths (first to last update), a lower bound on the service impact; the Stations and Routes pages measure the impact on actual trains.", ul);
  h("li", null, "Causes come from the alert text (signal, track, police/medical, mechanical, crowding, weather, ...).", ul);
}


// ---------------------------------------------------------------- line view (Marey chart + where time is lost)
async function linePage(idx, arg) {
  const root = app; root.replaceChildren();
  const avail = idx.lines_view || [];
  h("h1", null, "Line view: every train on the line", root);
  if (!avail.length) { h("div", "empty", "No line views yet: they need network-wide arrivals (all-stops collection or the subwaydata.nyc backfill).", root); return; }
  const [routeArg, dirArg] = (arg || "").split("_");
  const filters = h("div", "filters", null, root);
  h("label", "small secondary", "Line", filters); const sel = h("select", null, null, filters);
  avail.forEach(l => { const o = h("option", null, `${l.route} ${l.direction === "N" ? "northbound" : "southbound"}`, sel); o.value = `${l.route}_${l.direction}`; });
  const key = avail.some(l => `${l.route}_${l.direction}` === `${routeArg}_${dirArg}`) ? `${routeArg}_${dirArg}` : `${avail[0].route}_${avail[0].direction}`;
  sel.value = key; sel.addEventListener("change", () => { location.hash = `#/line/${sel.value}`; });
  const ctl = h("span", null, null, filters); ctl.style.marginLeft = "auto";
  let d;
  try { d = await load(`lines/${key}.json`); } catch (e) { h("div", "empty", `No data for ${key}.`, root); return; }
  const snap = d.snapshot, dev = d.deviation;
  const route = snap.route;
  let sim = null;
  try { const lv = await fetch(DATA + "live.json", { cache: "no-store" }); if (lv.ok) { const live = await lv.json(); sim = (live.simulation || []).find(e => e.route === route && e.direction === snap.direction) || null;
    if (sim && Math.abs((sim.scenarios.baseline.now || 0) - snap.now) > 1800) sim = null; } } catch (e) { /* optional */ }
  const simName = sid => { const st = sim && sim.scenarios.baseline.stops.find(x => x.stop_id === sid); return st ? st.name : sid; };
  const tiles = h("div", "tiles", null, root);
  const lateNow = snap.live.filter(t => t.lateness != null).map(t => t.lateness);
  tile(tiles, "Trains on the line", String(snap.live.filter(t => t.started).length), `${snap.actual.length} observed in the last 2 h`);
  tile(tiles, "Median lateness now", lateNow.length ? lateTxt(lateNow.sort((a, b) => a - b)[Math.floor(lateNow.length / 2)]) : "–", lateNow.length ? `${lateNow.filter(v => v >= 300).length} trains ≥5 min late` : "");
  const w = (dev.worst_stops || [])[0];
  tile(tiles, "Where time is lost", w ? w.name : "–", w ? `+${w.mean_delta_sec.toFixed(0)} s per train on average (${dev.n_trips} trips)` : "not enough history");
  tile(tiles, "Track changes", String(snap.live.filter(t => t.track_changed).length), "trains on a track other than scheduled");
  if (sim) { const wg = sim.scenarios.baseline.worst_gap, hg = sim.scenarios.hold_persists && sim.scenarios.hold_persists.worst_gap;
    tile(tiles, "Projected worst gap, next hour", wg ? `${(wg.gap_sec / 60).toFixed(0)} min` : "–", wg ? `at ${simName(wg.stop_id)} around ${hhmm(wg.at_ts)}${hg ? ` · ${(hg.gap_sec / 60).toFixed(0)} min if the hold persists` : ""}` : "no gap projected"); }
  h("h2", null, "Time-distance (Marey) chart", root);
  const card = h("div", "card", null, root);
  const legs = [{ stops: snap.stops, trains: [
    ...snap.scheduled.map(t => ({ trip_id: t.trip_id, route_id: route, points: t.points, kind: "sched" })),
    ...snap.actual.map(t => ({ trip_id: t.trip_id, train_id: t.train_id, route_id: route, points: t.points.map(p => [p[0], p[1]]), lateness_sec: t.last_lateness, kind: "actual" })),
    ...snap.live.filter(t => t.started).map(t => ({ trip_id: t.trip_id, train_id: t.train_id, route_id: route, points: t.points, lateness_sec: t.lateness, kind: "live" })),
    ...(sim ? sim.scenarios.baseline.trains.map(t => ({ trip_id: t.trip_id, train_id: t.train_id, route_id: route, points: t.points, lateness_sec: t.lateness_sec, kind: "sim" })) : []),
    ...(sim && sim.scenarios.hold_persists ? sim.scenarios.hold_persists.trains.filter(t => t.holding || t.stalled || t.knock_on_sec >= 60).map(t => ({ trip_id: t.trip_id, train_id: t.train_id, route_id: route, points: t.points, lateness_sec: t.lateness_sec, kind: "sim-hold" })) : []),
  ] }];
  stringline(card, { title: `${route} ${snap.direction === "N" ? "northbound" : "southbound"}: last 2 hours and the next hour`, subtitle: `solid: observed arrivals (green on time, amber ≥2 min late, red ≥5 min); dashed: the feed's projection for trains under way; grey: the timetable${sim ? "; dotted: the model's simulation (position-corrected ETAs, no overtaking)" : ""}${sim && sim.scenarios.hold_persists ? "; red dotted: if the current hold persists" : ""}`,
    legs, now: snap.now, horizonSec: 3600, backSec: 7200, highlight: new Set(), path: [], routeColor: () => ROUTE_COLORS[route] || null, rowH: 12 });
  h("div", "small secondary", "Read it like a railway dispatcher: parallel lines are regular service, converging lines are bunching, a flat stretch is a hold, and a widening white band is a gap. Compare the slope of observed lines with the grey timetable to see where trains run slower than planned.", card).style.marginTop = ".4rem";
  const liveBox = h("div", null, null, root);
  setupClientLive(ctl, liveBox, { feedKeys: sch => [(sch.route_feeds || {})[route]].filter(Boolean), render: (box, board, schedule, feeds) => renderLineLive(box, board, schedule, feeds, route, snap.direction) });
  if (sim) {
    const b = sim.scenarios.baseline, hp = sim.scenarios.hold_persists;
    const c = h("div", "card", null, root);
    h("h3", null, "Forward simulation from the current positions", c);
    h("p", "small", `As of ${hhmm(b.now)}: ${b.trains.length} trains projected over the next hour, ${b.n_knock_on} held back by the train ahead (${(b.knock_on_total_sec / 60).toFixed(0)} min of knock-on in total)${b.worst_gap ? `; largest projected gap ${(b.worst_gap.gap_sec / 60).toFixed(0)} min at ${simName(b.worst_gap.stop_id)} around ${hhmm(b.worst_gap.at_ts)}` : ""}.`
      + (hp ? ` If the current hold persists ${(hp.hold_extra_sec / 60).toFixed(0)} more minutes: ${hp.n_knock_on} trains held back (${(hp.knock_on_total_sec / 60).toFixed(0)} min)${hp.worst_gap ? `, largest gap ${(hp.worst_gap.gap_sec / 60).toFixed(0)} min at ${simName(hp.worst_gap.stop_id)}` : ""}; if it clears now: ${sim.scenarios.clears_now.n_knock_on} held back${sim.scenarios.clears_now.worst_gap ? `, largest gap ${(sim.scenarios.clears_now.worst_gap.gap_sec / 60).toFixed(0)} min` : ""}.` : " No train on the line is holding or stalled, so the baseline is the only scenario."), c);
    const held = b.trains.filter(t => t.holding || t.stalled);
    if (held.length) h("div", "small secondary", `Held or stalled now: ${held.map(t => `${(t.train_id || t.trip_id).trim()} (${lateTxt(t.lateness_sec)})`).join(", ")}.`, c);
    h("div", "tiny muted", "Each train's trajectory starts from its reported position (a train stopped longer than a normal dwell is at least that late, whatever its ETA says), uses the learned arrival model where available, and no train may arrive within 90 s of the train ahead; the knock-on is the delay this constraint adds to followers.", c);
  }
  if ((dev.grid || []).length && dev.n_trips) {
    h("h2", null, "Where the line loses time, by hour", root);
    const c2 = h("div", "card", null, root);
    heatmap(c2, { title: "Mean lateness change per stop vs the previous stop (seconds)", subtitle: `positive = time lost arriving at that stop; ${dev.n_trips} trips over the recent history`,
      rows: dev.stops.map(s => s.name), cols: HOURS, values: dev.grid.map(r => r.map(v => v == null ? 0 : Math.max(0, v))), format: fmt.sec, colLabelEvery: 3, rowLabelEvery: 1 });
    if ((dev.worst_stops || []).length) { const ul = h("ul", "small secondary", null, c2); dev.worst_stops.forEach(x => h("li", null, `${x.name}: +${x.mean_delta_sec.toFixed(0)} s per train`, ul)); }
  }
  try {
    const trust = await load("eta_trust.json");
    const tr = (trust.by_route || {})[route] || (trust.overall || []);
    if (tr.length) {
      h("h2", null, "How far ahead can the countdown clock be trusted?", root);
      const c3 = h("div", "card", null, root);
      barChart(c3, { title: `${route}: median and p90 absolute error of the feed's ETA by stops ahead`, subtitle: "error between the ETA shown when the train was k stops away and its actual arrival",
        categories: tr.map(x => `${x.stops_ahead} stop${x.stops_ahead > 1 ? "s" : ""}`), series: [{ name: "median", values: tr.map(x => x.median_abs_err_sec) }, { name: "p90", values: tr.map(x => x.p90_abs_err_sec) }], format: fmt.sec, height: 220 });
      h("div", "small secondary", `Bias (median signed error): ${tr.map(x => `${x.stops_ahead} stops ${x.bias_sec >= 0 ? "+" : "−"}${Math.abs(x.bias_sec).toFixed(0)} s`).join(" · ")}. Positive bias means trains arrive later than promised.`, c3);
    }
  } catch (e) { /* optional */ }
}


// ---------------------------------------------------------------- travel mode: origin -> destination, every viable path, live
const nyParts = ts => { const p = Object.fromEntries(new Intl.DateTimeFormat("en-US", { timeZone: NY, hour: "numeric", hour12: false, weekday: "short" }).formatToParts(new Date(ts * 1000)).map(x => [x.type, x.value]));
  return { hour: Number(p.hour) % 24, dow: ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].indexOf(p.weekday) }; };
const stateColor = s => s === "holding" || s === "stalled" ? "var(--status-critical)" : s === "stopped" || s === "terminal" ? "var(--status-warning)" : s === "unknown" ? "var(--de-emphasis)" : "var(--status-good)";
const mmss = s => { s = Math.max(0, Math.round(s)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; };
const setTile = (t, value, delta) => { t.querySelector(".value").textContent = value; let d = t.querySelector(".delta"); if (!d) d = h("div", "delta", null, t); d.textContent = delta || ""; };
const bullets = (routes, parent) => routes.forEach(r => routeBullet(r, parent));
const kmh = v => v == null ? "" : `${v.toFixed(0)} km/h`;
// what we can say about a train's speed: the last completed segment (exact, from feed timestamps) or the scheduled average on the current one
const speedText = t => { if (t.last_run && t.last_run.speed_kmh) return `last segment ${kmh(t.last_run.speed_kmh)}${t.last_run.sched_speed_kmh ? ` (sched ${kmh(t.last_run.sched_speed_kmh)})` : ""}`; if (t.segment && t.segment.sched_speed_kmh) return `~${kmh(t.segment.sched_speed_kmh)} sched · ${(t.segment.dist_m / 1000).toFixed(1)} km segment`; return ""; };

// searchable dropdown: items [{id, label, search}], render(item, li) draws the row, onPick(item) on selection
function combobox(parent, { placeholder, items, value, render, onPick, width = "280px" }) {
  const wrap = h("div", "combo", null, parent); wrap.style.width = width;
  const input = h("input", "combo-input", null, wrap); input.type = "text"; input.placeholder = placeholder || ""; input.autocomplete = "off"; input.setAttribute("role", "combobox"); input.setAttribute("aria-expanded", "false");
  const list = h("ul", "combo-list", null, wrap); list.hidden = true; list.setAttribute("role", "listbox");
  let active = -1, shown = [];
  const current = () => items.find(it => it.id === value);
  const show = q => { const s = (q || "").trim().toLowerCase(); shown = items.filter(it => !s || it.search.includes(s)).slice(0, 60); list.replaceChildren();
    shown.forEach(it => { const li = h("li", it.id === value ? "sel" : "", null, list); li.setAttribute("role", "option"); render(it, li); li.addEventListener("mousedown", ev => { ev.preventDefault(); pick(it); }); });
    if (!shown.length) h("li", "muted", "no station matches", list); list.hidden = false; input.setAttribute("aria-expanded", "true"); active = -1; };
  const hide = () => { list.hidden = true; input.setAttribute("aria-expanded", "false"); const cur = current(); input.value = cur ? cur.label : ""; };
  const pick = it => { value = it.id; hide(); onPick(it); };
  input.addEventListener("focus", () => { show(""); input.select(); });
  input.addEventListener("input", () => show(input.value));
  input.addEventListener("blur", () => setTimeout(hide, 150));
  input.addEventListener("keydown", ev => {
    if (ev.key === "Escape") { hide(); return; }
    if (list.hidden) { if (ev.key === "ArrowDown") show(input.value); return; }
    if (ev.key === "Enter") { ev.preventDefault(); if (active >= 0 && shown[active]) pick(shown[active]); else if (shown.length === 1) pick(shown[0]); return; }
    if (ev.key !== "ArrowDown" && ev.key !== "ArrowUp") return;
    ev.preventDefault(); active = ev.key === "ArrowDown" ? Math.min(shown.length - 1, active + 1) : Math.max(0, active - 1);
    [...list.children].forEach((li, i) => li.classList.toggle("active", i === active)); if (list.children[active]) list.children[active].scrollIntoView({ block: "nearest" }); });
  const cur = current(); if (cur) input.value = cur.label;
  return { input, wrap };
}

async function travelPage(idx, arg) {
  const root = app; root.replaceChildren();
  const head = h("div", "row between", null, root);
  h("h1", null, "Travel mode: every way to get there, live", head);
  const statusEl = h("div", "small secondary", "loading…", head);
  let schedule;
  try { schedule = await load("client_schedule.json"); } catch (e) { h("div", "empty", "Travel mode needs data/client_schedule.json, which the site build publishes.", root); return; }
  const [clines, holds, trust, clim, live, segs, cmodel] = await Promise.all([load("client_lines.json").catch(() => null), load("holds.json").catch(() => null), load("eta_trust.json").catch(() => null), load("climatology.json").catch(() => null), load("live.json").catch(() => null), load("segments.json").catch(() => null), load("client_model.json").catch(() => null)]);
  const segKey = (k, stop) => (segs && segs.by_key || {})[`${k}|${stop}`] || null;
  const lineSched = (clines && clines.lines) || {};
  const index = stationIndex(schedule);
  const stationsSorted = [...index.stations.values()].sort((a, b) => a.name.localeCompare(b.name) || a.routes.join().localeCompare(b.routes.join()));
  if (!stationsSorted.length) { h("div", "empty", "No line topology in the client schedule yet.", root); return; }
  const stLabel = s => `${s.name} (${s.routes.join(" ")})`;
  // selection: URL #/travel/<origin>/<destination>/<path>, then the last choice, then the first configured journey
  let saved = null; try { saved = JSON.parse(localStorage.getItem("travel2") || "null"); } catch {}
  let [oId, dId, pathId] = (arg || "").split("/").map(x => x ? decodeURIComponent(x) : x);
  if (!oId || !index.stations.has(oId)) {
    if (saved && index.stations.has(saved.oId)) ({ oId, dId } = saved);
    else { const j = (schedule.journeys || [])[0]; if (j) { oId = index.stationOf(j.legs[0].from_stop); dId = index.stationOf(j.legs[j.legs.length - 1].to_stop); } }
    if (!oId || !index.stations.has(oId)) oId = stationsSorted[0].id;
  }
  const reach = reachableStations(schedule, index, oId);
  if (!dId || !reach.has(dId)) { dId = saved && reach.has(saved.dId) ? saved.dId : ([...reach.entries()].find(([, e]) => e.how === "direct") || [...reach.keys()].map(k => [k]))[0]; }
  try { localStorage.setItem("travel2", JSON.stringify({ oId, dId })); } catch {}
  const go = (o, d, p) => { location.hash = `#/travel/${encodeURIComponent(o)}/${d ? encodeURIComponent(d) : ""}${p ? "/" + encodeURIComponent(p) : ""}`; };
  const origin = index.stations.get(oId), dest = dId ? index.stations.get(dId) : null;
  // controls: searchable station pickers; destinations say how they are reached from the origin
  const filters = h("div", "filters", null, root);
  const stationRow = (s, li, how) => { const top = h("div", null, null, li); h("strong", null, s.name, top); top.append(" "); s.routes.forEach(r => routeBullet(r, top)); if (how) h("div", "how", how, li); };
  const howText = e => { if (!e) return ""; if (e.direct.length) return `direct on the ${e.direct.join("/")}${e.via.length ? ` · or via ${e.via.slice(0, 2).map(v => `${v.r1} → ${v.r2s.join("/")} at ${v.station}`).join(", ")}${e.via.length > 2 ? ", …" : ""}` : ""}`;
    return `via ${e.via.slice(0, 3).map(v => `${v.r1} → ${v.r2s.join("/")} at ${v.station}`).join(" · ")}${e.via.length > 3 ? ` · +${e.via.length - 3} more` : ""}`; };
  h("label", "small secondary", "From", filters);
  combobox(filters, { placeholder: "type a station…", value: oId, items: stationsSorted.map(s => ({ id: s.id, label: stLabel(s), search: `${s.name} ${s.routes.join(" ")}`.toLowerCase(), s })),
    render: (it, li) => stationRow(it.s, li, `${it.s.routes.length} line${it.s.routes.length > 1 ? "s" : ""}`), onPick: it => { if (it.id !== oId) go(it.id, "", ""); } });
  h("label", "small secondary", "To", filters);
  const destItems = stationsSorted.filter(s => reach.has(s.id)).map(s => { const e = reach.get(s.id); return { id: s.id, label: stLabel(s), how: e.how, search: `${s.name} ${s.routes.join(" ")} ${e.how === "direct" ? "direct" : "transfer"} ${e.via.map(v => `${v.r1} ${v.r2s.join(" ")} ${v.station}`).join(" ")}`.toLowerCase(), s, e }; })
    .sort((a, b) => (a.how === b.how ? 0 : a.how === "direct" ? -1 : 1) || a.s.name.localeCompare(b.s.name));
  combobox(filters, { placeholder: "type a destination…", value: dId, items: destItems, width: "360px",
    render: (it, li) => { stationRow(it.s, li, howText(it.e)); if (it.how !== "direct") li.classList.add("xfer"); }, onPick: it => { if (it.id !== dId) go(oId, it.id, ""); } });
  const swap = h("button", "icon-btn", "⇄ reverse", filters); swap.title = "Swap origin and destination";
  swap.addEventListener("click", () => { if (dId) go(dId, oId, ""); });
  h("span", "small secondary", `${reach.size} stations reachable from ${origin.name}: ${[...reach.values()].filter(x => x.how === "direct").length} directly, ${[...reach.values()].filter(x => x.how !== "direct").length} with one change`, filters);
  if (!dest) { h("div", "empty", "Choose a destination.", root); return; }
  const paths = enumeratePaths(schedule, index, oId, dId, 8);
  if (!paths.length) { h("div", "empty", `No path with at most one transfer from ${origin.name} to ${dest.name} in the exported lines.`, root); return; }
  const nowRef = () => (schedule.demo_now || Date.now() / 1000);
  const parts = nyParts(nowRef());
  // per-line history for the lines the paths use (deviation grid = typical time lost per stop at this hour)
  const keysUsed = [...new Set(paths.flatMap(p => p.legs.map(l => l.keys[0])))];
  const devByKey = {};
  await Promise.all(keysUsed.map(async k => { try { const d = await load(`lines/${k}.json`); devByKey[k] = d; } catch (e) { devByKey[k] = null; } }));
  const holdMap = new Map(((holds && holds.by_stop) || []).map(x => [x.stop_id, x]));
  const typicalAt = (key, i) => { const d = devByKey[key] && devByKey[key].deviation; if (!d) return null; const line = schedule.lines[key]; const r = (d.stops || []).findIndex(s => s.stop_id === line.stops[i]); const v = r >= 0 && d.grid[r] ? d.grid[r][parts.hour] : null; return v == null ? null : v; };
  const legTypical = leg => { const k = leg.keys[0], [fi, ti] = leg.idx[k]; let s = 0, any = false; for (let i = fi + 1; i <= ti; i++) { const v = typicalAt(k, i); if (v != null) { s += v; any = true; } } return any ? s : null; };
  const legHoldRisk = leg => { const k = leg.keys[0], line = schedule.lines[k], [fi, ti] = leg.idx[k]; const trips = Math.max(60, (lineSched[k] || []).length); let s = 0; for (let i = fi + 1; i <= ti; i++) { const hx = holdMap.get(line.stops[i]); if (hx) s += hx.per_day * hx.median_sec / trips; } return s; };
  const alertsNow = (live && live.alerts) || [];
  const routeAlerts = routes => alertsNow.filter(a => (a.routes || []).some(r => routes.includes(r)) && !a.planned);
  const now0 = nowRef();
  for (const p of paths) {
    p.wait1_sec = (() => { const hw = schedHeadwayAt(schedule, lineSched, p.legs[0].keys, p.legs[0].from, now0); return hw ? Math.min(hw / 2, 900) : 300; })();
    p.wait2_sec = p.transfer ? (() => { const hw = schedHeadwayAt(schedule, lineSched, p.legs[1].keys, p.legs[1].from, now0); return hw ? Math.min(hw / 2, 900) : 300; })() : 0;
    p.legs.forEach(l => { l.typical_sec = legTypical(l); l.hold_risk_sec = legHoldRisk(l); l.alerts = routeAlerts(l.routes); });
    p.typical_sec = p.legs.reduce((a, l) => a + (l.typical_sec || 0), 0); p.hold_risk_sec = p.legs.reduce((a, l) => a + l.hold_risk_sec, 0);
    p.expected_sec = p.wait1_sec + p.sched_sec + p.typical_sec + p.hold_risk_sec + p.wait2_sec;
    p.n_alerts = p.legs.reduce((a, l) => a + l.alerts.length, 0);
    p.live = null;
  }
  const rankKey = p => p.live ? p.live.arrive_ts : Infinity;
  const rank = () => paths.slice().sort((a, b) => (paths.some(p => p.live) ? rankKey(a) - rankKey(b) || a.expected_sec - b.expected_sec : a.expected_sec - b.expected_sec));
  let selected = paths.find(p => p.id === pathId) || rank()[0];

  // tiles
  const tiles = h("div", "tiles", null, root);
  const tNext = tile(tiles, "Next train", "–", " "), tArr = tile(tiles, `Arrive ${dest.name}`, "–", " "), tLine = tile(tiles, "On your lines now", "–", " "), tTyp = tile(tiles, "This hour, typically", "–", " ");
  // hold scenarios (shown when a train on these lines is held): what the prediction engine assumes about the hold
  const scen = h("div", "row modebar", null, root); scen.style.display = "none";
  h("span", "small secondary", "A train on these lines is held. Times assume:", scen);
  const scenBtns = {};
  [["baseline", "the hold ends as holds here usually do"], ["hold_persists", "it drags on (p90)"], ["clears_now", "it clears now"]].forEach(([id, name]) => {
    const b = h("button", `icon-btn${id === "baseline" ? " on" : ""}`, name, scen); scenBtns[id] = b;
    b.addEventListener("click", () => { state.scenario = id; Object.entries(scenBtns).forEach(([k, x]) => x.classList.toggle("on", k === id)); onBoards(); }); });
  // ranked paths
  h("h2", null, `${origin.name} → ${dest.name}: ${paths.length} way${paths.length > 1 ? "s" : ""} to get there`, root);
  const pathsCard = h("div", "card", null, root); const pathsBody = h("div", null, null, pathsCard);
  h("div", "tiny muted", "Bars: expected door-to-door time, broken into wait (grey), ride (line colour, including the time trains typically lose on that stretch at this hour and the hold risk), walk at the transfer (dark). Ranking uses the live feeds once they arrive (arrival time of the next itinerary), otherwise the expected time. Click a path to inspect it below.", pathsCard);
  const detail = h("div", null, null, root);
  let savedMode = "track"; try { savedMode = localStorage.getItem("travel_mode") || "track"; } catch {}
  const state = { boards: {}, lbNow: null, demo: !!schedule.demo_now, alerts: null, diagram: null, tracks: null, pred: {}, predBoards: {}, scenario: "baseline", anyHeld: false, mode: savedMode, countdowns: [] };
  // the prediction engine's 80% window and the feed's own ETA at a leg's arrival stop
  const arriveRange = l => { if (!l || !l.pred) return null; const pt = l.pred.points.find(x => Math.abs(x.eta_ts - l.arrive_ts) < 0.5); if (!pt) return null; const fp = (l.feed_points || []).find(p => p[0] === pt.idx); return { lo: pt.lo_ts, hi: pt.hi_ts, feed: fp ? fp[1] : null, source: pt.source }; };
  const rangeText = (rg, ts) => rg ? `${hhmm(rg.lo)}–${hhmm(rg.hi)}${rg.feed != null && Math.abs(rg.feed - ts) >= 60 ? ` · feed says ${hhmm(rg.feed)}` : ""}` : "";

  function drawPaths() {
    pathsBody.replaceChildren();
    const ranked = rank(); const scale = Math.max(...paths.map(p => Math.max(p.expected_sec, p.live ? p.live.total_sec : 0)), 60);
    ranked.forEach((p, i) => {
      const row = h("div", `pathrow${p === selected ? " sel" : ""}`, null, pathsBody); row.addEventListener("click", () => { selected = p; go(oId, dId, p.id); });
      const top = h("div", "row between", null, row); const lhs = h("div", "row", null, top); h("span", "rank", `${i + 1}`, lhs);
      p.legs.forEach((l, li) => { if (li) lhs.append(" → "); bullets(l.routes, lhs); }); h("span", "small secondary", p.transfer ? ` change at ${p.transfer.station}` : " direct", lhs);
      if (p.n_alerts) { const c = h("span", "status-chip st-degraded", null, lhs); h("span", "dot", null, c); c.append(`${p.n_alerts} alert${p.n_alerts > 1 ? "s" : ""}`); }
      if (p.live && p.live.legs.some(l => l.position && (l.position.holding || l.position.stalled))) { const c = h("span", "status-chip st-disrupted", null, lhs); h("span", "dot", null, c); c.append("train held"); }
      const nums = h("div", "small nums", null, top);
      nums.append(p.live ? `live ${minTxt(p.live.total_sec)} (arrive ${hhmm(p.live.arrive_ts)}) · ` : (state.lbNow ? "no train in the feed · " : ""), `expected ${minTxt(p.expected_sec)} · scheduled ${minTxt(p.sched_sec)}`);
      const bar = h("div", "pathbar", null, row);
      const seg = (sec, color, title) => { if (!(sec > 0)) return; const s = h("span", null, null, bar); s.style.width = `${(sec / scale) * 100}%`; s.style.background = color; s.title = title; };
      seg(p.wait1_sec, "var(--de-emphasis)", `wait ${minTxt(p.wait1_sec)} (half the scheduled headway)`);
      seg((p.legs[0].sched_ride_sec || 0) + (p.legs[0].typical_sec || 0) + p.legs[0].hold_risk_sec, ROUTE_COLORS[p.legs[0].routes[0]] || "var(--series-1)", `${p.legs[0].routes.join("/")}: ${minTxt(p.legs[0].sched_ride_sec)} scheduled${p.legs[0].typical_sec != null ? ` ${p.legs[0].typical_sec >= 0 ? "+" : "−"}${Math.abs(p.legs[0].typical_sec).toFixed(0)} s typical` : ""}${p.legs[0].hold_risk_sec >= 5 ? ` +${p.legs[0].hold_risk_sec.toFixed(0)} s hold risk` : ""}`);
      if (p.transfer) { seg(p.transfer.walk_sec, "var(--text-secondary)", `walk ${minTxt(p.transfer.walk_sec)} at ${p.transfer.station}`); seg(p.wait2_sec, "var(--de-emphasis)", `wait ${minTxt(p.wait2_sec)} for the ${p.legs[1].routes.join("/")}`);
        seg((p.legs[1].sched_ride_sec || 0) + (p.legs[1].typical_sec || 0) + p.legs[1].hold_risk_sec, ROUTE_COLORS[p.legs[1].routes[0]] || "var(--series-2)", `${p.legs[1].routes.join("/")}: ${minTxt(p.legs[1].sched_ride_sec)} scheduled${p.legs[1].typical_sec != null ? ` ${p.legs[1].typical_sec >= 0 ? "+" : "−"}${Math.abs(p.legs[1].typical_sec).toFixed(0)} s typical` : ""}`); }
      if (p.live) { const mk = h("span", "livemark", null, bar); mk.style.left = `${Math.min(100, (p.live.total_sec / scale) * 100)}%`; mk.title = `live: ${minTxt(p.live.total_sec)}`; }
      const why = h("div", "tiny muted", null, row);
      const bits = [];
      p.legs.forEach(l => { if (l.typical_sec != null && Math.abs(l.typical_sec) >= 20) bits.push(`${l.routes.join("/")} stretch typically ${l.typical_sec >= 0 ? "loses" : "gains"} ${Math.abs(l.typical_sec).toFixed(0)} s at this hour`); if (l.hold_risk_sec >= 10) bits.push(`${l.hold_risk_sec.toFixed(0)} s expected hold risk on the ${l.routes.join("/")}`); l.alerts.slice(0, 1).forEach(al => bits.push(`alert: ${al.header}`)); });
      if (p.live && p.live.connection_margin_sec != null) bits.push(`${p.live.connection_margin_sec < 120 ? "tight" : "comfortable"} connection (${minTxt(p.live.connection_margin_sec)} margin${p.live.next_if_missed_sec != null ? `, next in ${minTxt(p.live.next_if_missed_sec)} if missed` : ""})`);
      why.textContent = bits.join(" · ");
    });
  }

  // selected path detail: horizontal diagram (one track per leg), trains table, insights, Marey chart
  function drawDetail() {
    detail.replaceChildren();
    const p = selected;
    const modeBar = h("div", "row modebar", null, detail); h("span", "small secondary", "View", modeBar);
    const modeBtns = {};
    [["track", "Track"], ["timeline", "Timeline"], ["board", "Departure board"]].forEach(([id, name]) => { const b = h("button", `icon-btn${state.mode === id ? " on" : ""}`, name, modeBar); modeBtns[id] = b;
      b.addEventListener("click", () => { state.mode = id; try { localStorage.setItem("travel_mode", id); } catch {} Object.entries(modeBtns).forEach(([k, x]) => x.classList.toggle("on", k === id)); applyMode(); }); });
    const card = h("div", "card", null, detail);
    state.boardBox = h("div", "card", null, detail); state.stringBox = h("div", "card", null, detail);
    const applyMode = () => { card.style.display = state.mode === "track" ? "" : "none"; state.boardBox.style.display = state.mode === "board" ? "" : "none"; state.stringBox.style.display = state.mode === "timeline" ? "" : "none"; };
    applyMode();
    const th = h("div", "row between", null, card); const tt = h("div", null, null, th);
    p.legs.forEach((l, li) => { if (li) tt.append(" → "); bullets(l.routes, tt); }); h("strong", null, ` ${origin.name} → ${dest.name}${p.transfer ? ` via ${p.transfer.station}` : ""}`, tt);
    const layerBox = h("div", "layers", null, th);
    const layerOn = { typical: true, holds: !!(holds && holds.n), speed: true };
    const tracks = p.legs.map((l, li) => { const k = l.keys[0], line = schedule.lines[k], [fi, ti] = l.idx[k];
      const stops = line.stops.map((s, i) => ({ stop_id: s, name: line.names[i] })); const typical = stops.map((s, i) => { const v = typicalAt(k, i); return v == null ? null : Math.max(0, v); }); const hpd = stops.map(s => (holdMap.get(s.stop_id) || {}).per_day ?? null);
      // realized speed arriving at each stop (this hour when there are enough runs, else overall), against the scheduled average
      const speed = stops.map((s, i) => { const sg = segKey(k, s.stop_id); if (!sg || !sg.dist_m) return null; const hr = sg.by_hour_run_sec && sg.by_hour_run_sec[parts.hour]; const run = hr || sg.median_run_sec; return run ? sg.dist_m / run * 3.6 : null; });
      const schedSpeed = stops.map((s, i) => i > 0 && line.dist_m && line.dist_m[i - 1] && line.run_sec[i - 1] ? line.dist_m[i - 1] / line.run_sec[i - 1] * 3.6 : null);
      return { key: k, keys: l.keys, stops, fromIdx: fi, toIdx: ti, startIdx: Math.max(0, fi - (li === 0 ? 5 : 4)), endIdx: ti, route: l.routes[0], routesLabel: l.routes.join("/"), color: ROUTE_COLORS[l.routes[0]] || null,
        layersAll: [{ id: "typical", name: "typical +s", values: typical, max: Math.max(30, ...typical.map(v => v || 0)), color: "var(--series-1)", format: v => `+${v.toFixed(0)}` }, { id: "holds", name: "holds/day", values: hpd, max: Math.max(1, ...hpd.map(v => v || 0)), color: "var(--status-warning)", format: v => v.toFixed(1) },
          { id: "speed", name: "km/h", values: speed.some(v => v != null) ? speed : schedSpeed, max: Math.max(40, ...schedSpeed.map(v => v || 0)), color: speed.some(v => v != null) ? "var(--series-3)" : "var(--de-emphasis)", format: v => v.toFixed(0) }] }; });
    state.tracks = tracks;
    const targets = new Set((idx.targets || []).map(t => t.stop_id));
    const draw = () => { if (state.diagram) state.diagram.root.remove();
      tracks.forEach(t => { t.layers = t.layersAll.filter(L => layerOn[L.id]); });
      state.diagram = trackDiagram(card, { tracks, targets, link: p.transfer ? { from: [0, tracks[0].toIdx], to: [1, tracks[1].fromIdx], label: `walk ${minTxt(p.transfer.walk_sec)}` } : null });
      legend.remove(); card.append(legend); tickDiagram(); };
    [["typical", "typical +s"], ["holds", "holds/day"], ["speed", segs && segs.n ? "km/h (measured)" : "km/h (scheduled)"]].forEach(([id, name]) => { const lab = h("label", null, null, layerBox); const cb = h("input", null, null, lab); cb.type = "checkbox"; cb.checked = layerOn[id]; lab.append(` ${name}`); cb.addEventListener("change", () => { layerOn[id] = cb.checked; draw(); }); });
    const legend = h("div", "tiny muted", `Left to right in the direction of travel. Markers carry the line and glide between 30-second polls along the scheduled running time: green moving, amber stopped, red holding or stalled, grey no position; the ringed markers are the trains of the recommended itinerary (black ring: your first train, purple: the connection). ◎ monitored platform. Bars under the stops: typical time lost arriving there at this hour, holds per day, and the speed on the segment arriving there (${segs && segs.n ? "measured from feed timestamps and track distances, this hour where there are enough runs" : "scheduled average from track distances; measured speeds appear once the collector has logged runs"}). The feeds carry no GPS: a train's speed on its current segment is only known once it arrives, so the label shows its last completed segment.`, card);
    draw();
    const tblCard = h("div", "card", null, detail); const tblHead = h("div", "row between", null, tblCard); h("strong", null, "Your next itineraries on this path", tblHead); state.tblAge = h("span", "small secondary", "", tblHead); state.tblBody = h("div", null, null, tblCard);
    const ins = h("div", "card", null, detail); h("strong", null, "What our data says about this path", ins); const ul = h("ul", "small secondary", null, ins);
    p.legs.forEach(l => { const k = l.keys[0], line = schedule.lines[k], [fi, ti] = l.idx[k];
      const worst = []; for (let i = fi + 1; i <= ti; i++) { const v = typicalAt(k, i); if (v != null) worst.push([v, i]); } worst.sort((a, b) => b[0] - a[0]);
      if (worst[0] && worst[0][0] > 0) h("li", null, `${l.routes.join("/")}: at this hour trains lose the most time arriving at ${line.names[worst[0][1]]} (+${worst[0][0].toFixed(0)} s per train); the stretch typically ${l.typical_sec >= 0 ? "loses" : "gains"} ${Math.abs(l.typical_sec).toFixed(0)} s in total.`, ul);
      const hw = []; for (let i = fi; i <= ti; i++) { const hx = holdMap.get(line.stops[i]); if (hx) hw.push([hx.per_day, i, hx]); } hw.sort((a, b) => b[0] - a[0]);
      if (hw[0]) h("li", null, `${l.routes.join("/")}: trains get held most at ${line.names[hw[0][1]]} (${hw[0][2].per_day.toFixed(1)} holds/day, median ${(hw[0][2].median_sec / 60).toFixed(1)} min${hw[0][2].worst_hours && hw[0][2].worst_hours.length ? `, mostly around ${hw[0][2].worst_hours.map(x => `${String(x).padStart(2, "0")}:00`).join(", ")}` : ""}).`, ul);
      const slow = []; for (let i = fi + 1; i <= ti; i++) { const sg = segKey(k, line.stops[i]); if (sg && sg.ratio && sg.consecutive) slow.push(sg); } slow.sort((a, b) => b.ratio - a.ratio);
      if (slow[0] && slow[0].ratio >= 1.15) h("li", null, `${l.routes[0]}: the slowest measured segment is ${slow[0].from_name} → ${slow[0].to_name}, ${kmh(slow[0].speed_kmh)} against a scheduled ${kmh(slow[0].sched_speed_kmh)} (${((slow[0].ratio - 1) * 100).toFixed(0)}% longer than planned over ${slow[0].n} runs).`, ul);
      else if (slow.length) h("li", null, `${l.routes[0]}: measured running times on this stretch are within ${((Math.max(...slow.map(x => x.ratio)) - 1) * 100).toFixed(0)}% of schedule (${slow.reduce((a, x) => a + x.n, 0)} runs).`, ul);
      l.alerts.slice(0, 2).forEach(al => h("li", null, `Alert on the ${(al.routes || []).join("/")}: ${al.header}`, ul));
      const sim = live && (live.simulation || []).find(e => `${e.route}_${e.direction}` === k); if (sim && sim.scenarios.baseline.worst_gap) { const wg = sim.scenarios.baseline.worst_gap; const nm = (sim.scenarios.baseline.stops.find(s => s.stop_id === wg.stop_id) || {}).name || wg.stop_id; h("li", null, `${l.routes[0]}: the forward simulation projects the largest gap at ${nm} (${(wg.gap_sec / 60).toFixed(0)} min around ${hhmm(wg.at_ts)})${sim.disturbed && sim.scenarios.hold_persists ? `; if the current hold persists 10 more minutes ${sim.scenarios.hold_persists.n_knock_on} trains are held back` : ""}.`, ul); }
      for (let i = fi; i <= ti; i++) { const st = live && (live.stations || []).find(s => s.stop_id === line.stops[i]); if (st) { (st.effects || []).slice(0, 1).forEach(e => h("li", null, `${line.names[i]} (monitored): ${e.text}`, ul)); if (st.scenarios && st.scenarios.headline) h("li", null, `${st.scenarios.headline}.`, ul); } } });
    if (p.transfer) { const hx = holdMap.get(p.transfer.stop2) || holdMap.get(p.transfer.stop); if (hx) h("li", null, `At the transfer, ${p.transfer.station}, trains are held ${hx.per_day.toFixed(1)} times a day (median ${(hx.median_sec / 60).toFixed(1)} min).`, ul); h("li", null, `The scheduled headway of the ${p.legs[1].routes.join("/")} at ${p.transfer.station} is about ${schedHeadwayAt(schedule, lineSched, p.legs[1].keys, p.legs[1].from, now0) ? `${(schedHeadwayAt(schedule, lineSched, p.legs[1].keys, p.legs[1].from, now0) / 60).toFixed(0)} min` : "unknown"}, so an expected wait of ${minTxt(p.wait2_sec)}.`, ul); }
    const baseRate = clim && clim.grid_by_route ? p.legs.map(l => { const g = clim.grid_by_route[l.routes[0]]; return g && g[parts.dow] ? [l.routes[0], g[parts.dow][parts.hour]] : null; }).filter(Boolean) : [];
    if (baseRate.length) h("li", null, `Disruption base rate for this day and hour: ${baseRate.map(([r, v]) => `${r} ${v.toFixed(2)}/week`).join(", ")}.`, ul);
    if (cmodel) { const cal = cmodel.eta_calibration || {}; const r0 = p.legs[0].routes[0]; const tbl = (cal.by_route || {})[r0]; const b = (tbl || cal.all || [])[1];
      h("li", null, `Prediction engine: the feed's ETAs are calibrated on ${fmt.compact(cal.n || 0)} samples${tbl && b ? ` (${r0}, 2–5 min out: the train comes ${Math.abs(b.bias).toFixed(0)} s ${b.bias >= 0 ? "later" : "earlier"} than promised on average, 80% within ${b.p10.toFixed(0)}…+${b.p90.toFixed(0)} s)` : " (no table for this line yet: priors)"}, blended with the lateness the timetable carries from the train's current stop${(cmodel.hold_survival || {}).n_holds ? `; a held train is expected to move again after the remaining hold typical of ${fmt.compact(cmodel.hold_survival.n_holds)} logged holds` : ""}.`, ul); }
    if (!ul.children.length) h("li", null, "No history for these stretches yet; the hourly collection fills this in.", ul);
    renderLive();
  }

  // live layer
  const involvedKeys = [...new Set(paths.flatMap(p => p.legs.flatMap(l => l.keys)))];
  const feedKeys = [...new Set(involvedKeys.map(k => (schedule.route_feeds || {})[k.split("_")[0]]).filter(Boolean))];
  const progressOnTrack = (t, key, track, age) => { const line = schedule.lines[key], p = trainProgress(t, age, line); const j = Math.floor(p.idx), frac = p.idx - j;
    const ti = track.stops.findIndex(s => s.stop_id === line.stops[j]); if (ti < 0) return null; const ti2 = j + 1 < line.stops.length ? track.stops.findIndex(s => s.stop_id === line.stops[j + 1]) : -1;
    return { ...p, idx: ti + frac * (ti2 > ti ? ti2 - ti : 1) }; };
  function tickDiagram() {
    if (!state.diagram || !state.tracks || !state.lbNow) return;
    const age = state.demo ? 0 : nowRef() - state.lbNow; const best = selected.live;
    const out = [];
    state.tracks.forEach((track, li) => { for (const k of track.keys) { const lb = state.boards[k]; if (!lb) continue; const route = k.split("_")[0];
      for (const t of lb.trains) { const p = progressOnTrack(t, k, track, age); if (!p) continue; const late = t.effective_lateness_sec ?? t.lateness_sec; const mine = best && best.legs[li] && best.legs[li].trip_id === t.trip_id;
        out.push({ id: t.trip_id, track: li, idx: p.idx, state: p.state, route, color: stateColor(p.state), emphasis: mine ? (li === 0 ? "origin" : "connection") : null,
          label: `${(t.train_id || t.trip_id).trim().replace(/\s+/g, " ").slice(0, 16)}${mine ? (li === 0 ? " · yours" : " · connection") : ""}`, sub: `${late == null ? "" : lateTxt(late)}${p.state === "holding" ? ` · held ${mmss(p.since)}` : p.state === "stalled" ? ` · stalled ${mmss(p.since)}` : ""}${t.last_run && t.last_run.speed_kmh ? ` · ${kmh(t.last_run.speed_kmh)}` : t.segment && t.segment.sched_speed_kmh && p.state === "moving" ? ` · ~${kmh(t.segment.sched_speed_kmh)}` : ""}` }); } } });
    state.diagram.update(out);
  }
  function tickTiles() {
    const now = nowRef(), p = selected, it = p.live;
    setTile(tNext, it ? mmss(it.board_ts - now) : "–", it ? `${it.legs[0].route} boards ${hhmm(it.board_ts)} at ${origin.name}${it.legs[0].position ? ` · now ${posText(it.legs[0].position)}` : ""}` : (state.lbNow ? "no train in the feed for this path" : "waiting for the feeds"));
    tNext.querySelector(".value").classList.add("countdown");
    const rg = it ? arriveRange(it.legs[it.legs.length - 1]) : null;
    setTile(tArr, it ? hhmm(it.arrive_ts) : "–", it ? `${rg ? `80% window ${rangeText(rg, it.arrive_ts)} · ` : ""}door to door ${minTxt(it.total_sec)} · expected ${minTxt(p.expected_sec)} · scheduled ${minTxt(p.sched_sec)}` : `expected ${minTxt(p.expected_sec)} · scheduled ${minTxt(p.sched_sec)}`);
    for (const [el, ts] of state.countdowns) el.textContent = mmss(Math.max(0, ts - now));
  }
  function renderLive() {
    const now = nowRef(); tickTiles(); tickDiagram();
    const boards = Object.values(state.boards); const trains = boards.reduce((a, b) => a + b.trains.length, 0);
    setTile(tLine, state.lbNow ? `${trains} trains` : "–", state.lbNow ? `${boards.reduce((a, b) => a + b.n_holding, 0)} holding · ${boards.reduce((a, b) => a + b.n_stalled, 0)} stalled · feed optimistic for ${boards.reduce((a, b) => a + b.n_feed_optimistic, 0)} · ${involvedKeys.map(k => k.split("_")[0]).filter((r, i, arr) => arr.indexOf(r) === i).join(" ")}` : "waiting for the feeds");
    setTile(tTyp, `${selected.typical_sec >= 0 ? "+" : "−"}${Math.abs(selected.typical_sec).toFixed(0)} s`, `time lost on this path's stretches at this hour${selected.hold_risk_sec >= 5 ? ` · +${selected.hold_risk_sec.toFixed(0)} s hold risk` : ""}${selected.n_alerts ? ` · ${selected.n_alerts} alert(s) on its lines` : ""}`);
    if (!state.tblBody) return;
    state.tblBody.replaceChildren(); state.tblAge.textContent = state.lbNow ? `${state.demo ? "recorded snapshot at" : "feeds polled"} ${hhmmss(state.lbNow)} ET` : "";
    const its = state.lbNow ? pathTrips(state.predBoards, schedule, selected, now) : [];
    if (!its.length) { h("div", "small secondary", state.lbNow ? "No itinerary on this path in the feed within the hour (overnight reroute, or the line is not running this pattern right now)." : "Waiting for the feeds…", state.tblBody); }
    else {
      const wrap = h("div", "table-wrap", null, state.tblBody); const tb = h("table", "tiny", null, wrap); const tr = h("tr", null, null, h("thead", null, null, tb));
      const cols = ["leave in", `${selected.legs[0].routes.join("/")} train`, "boards", `arrive ${selected.transfer ? selected.transfer.station : dest.name}`]; if (selected.transfer) cols.push("connection", `${selected.legs[1].routes.join("/")} train`, "boards", `arrive ${dest.name}`); cols.push("total", "vs schedule");
      cols.forEach(x => h("th", /^(boards|arrive|total|vs|leave)/.test(x) ? "num" : "", x, tr)); const body = h("tbody", null, null, tb);
      its.forEach((it, i) => { const row = h("tr", i === 0 ? "worse" : "", null, body); h("td", "num", mmss(Math.max(0, it.board_ts - now)), row);
        it.legs.forEach((l, li) => { if (li === 1) { const cc = h("td", "small", null, row); cc.append(`walk ${minTxt(it.walk_sec)}, wait ${minTxt(it.wait_at_transfer_sec - it.walk_sec)}`); if (it.connection_margin_sec != null && it.connection_margin_sec < 120) { const c = h("span", "status-chip st-degraded", null, cc); c.style.marginLeft = ".3rem"; h("span", "dot", null, c); c.append("tight"); } }
          const c = h("td", "small", null, row); routeBullet(l.route, c); c.append(` ${(l.train_id || l.trip_id).trim()}`); c.append(" "); if (l.position) { const sp = h("span", "tiny muted", `${posText(l.position)}${speedText(l) ? ` · ${speedText(l)}` : ""}`, c); sp.style.display = "block"; } posFlags(c, l.position, l.corroboration, { track_changed: l.track_changed });
          h("td", "num eta", hhmm(l.board_ts), row); const ac = h("td", "num eta", hhmm(l.arrive_ts), row); const rg = arriveRange(l); if (rg) { const s = h("div", "tiny muted", rangeText(rg, l.arrive_ts), ac); s.style.whiteSpace = "nowrap"; } });
        h("td", "num", minTxt(it.total_sec), row); h("td", "num", it.ride_vs_sched_sec == null ? "–" : `${it.ride_vs_sched_sec >= 0 ? "+" : "−"}${Math.abs(it.ride_vs_sched_sec / 60).toFixed(0)} min`, row); });
      h("div", "tiny muted", "Times come from the prediction engine: the feed's ETA calibrated by line and horizon, blended with the lateness the timetable carries from the train's current stop, and corrected for a train that is being held; the small range is the 80% window. Lateness and flags come from the train's reported position. The first row is the recommended itinerary; a connection is tight when the margin after the walk is under two minutes.", state.tblBody);
    }
    // departure-board view: the next trains from the origin, big and glanceable
    if (state.boardBox) {
      state.boardBox.replaceChildren(); state.countdowns = [];
      const bh = h("div", "row between", null, state.boardBox); h("strong", null, `Departures from ${origin.name}`, bh);
      h("span", "small secondary", state.lbNow ? `${selected.legs[0].routes.join("/")} toward ${dest.name}${selected.transfer ? `, change at ${selected.transfer.station}` : ""}` : "waiting for the feeds", bh);
      if (!its.length) h("div", "small secondary", state.lbNow ? "No train for this path in the feed yet." : "Waiting for the feeds…", state.boardBox);
      its.slice(0, 5).forEach((it, i) => { const l0 = it.legs[0], ln = it.legs[it.legs.length - 1]; const row = h("div", `dep${i === 0 ? " first" : ""}`, null, state.boardBox);
        const cd = h("div", "dep-count countdown", mmss(Math.max(0, it.board_ts - now)), row); state.countdowns.push([cd, it.board_ts]);
        const mid = h("div", "dep-mid", null, row); const top = h("div", null, null, mid); routeBullet(l0.route, top); top.append(` ${(l0.train_id || l0.trip_id).trim()} · boards ${hhmm(l0.board_ts)} `); posFlags(top, l0.position, l0.corroboration);
        const bits = [l0.position ? posText(l0.position) : null, it.legs[1] ? `change at ${selected.transfer.station}: ${it.connection_margin_sec != null && it.connection_margin_sec < 120 ? "tight, " : ""}${minTxt(it.connection_margin_sec)} margin` : null,
          it.ride_vs_sched_sec != null && Math.abs(it.ride_vs_sched_sec) >= 60 ? `${it.ride_vs_sched_sec > 0 ? "+" : "−"}${Math.abs(it.ride_vs_sched_sec / 60).toFixed(0)} min vs schedule` : null].filter(Boolean);
        h("div", "tiny muted", bits.join(" · "), mid);
        const arr = h("div", "dep-arr", null, row); h("div", "eta", hhmm(it.arrive_ts), arr); const rg = arriveRange(ln); h("div", "tiny muted", rg ? rangeText(rg, it.arrive_ts) : `arrive ${dest.name}`, arr); });
      h("div", "tiny muted", "Countdown to boarding at your platform; the arrival is the prediction engine's estimate with its 80% window.", state.boardBox);
    }
    // Marey chart of the path over the next 45 minutes
    if (state.stringBox && state.tracks) {
      state.stringBox.replaceChildren();
      const legs = state.tracks.map((track, li) => { const seg = track.stops.slice(track.startIdx, track.endIdx + 1); const trs = [];
        for (const k of track.keys) { const lb = state.boards[k]; if (!lb) continue; const line = schedule.lines[k];
          for (const t of lb.trains) { const pts = t.points.map(([i, ts]) => [track.stops.findIndex(s => s.stop_id === line.stops[i]) - track.startIdx, ts]).filter(p => p[0] >= 0 && p[0] <= track.endIdx - track.startIdx); if (pts.length >= 2) trs.push({ trip_id: t.trip_id, train_id: t.train_id, route_id: k.split("_")[0], points: pts, lateness_sec: t.effective_lateness_sec ?? t.lateness_sec, kind: t.position && (t.position.holding || t.position.stalled) ? "live-hold" : "live" }); } }
        return { stops: seg, trains: trs }; });
      legs.forEach((lg, li) => { const track = state.tracks[li]; for (const k of track.keys) { const pr = state.pred[k]; const sc = pr && (pr[state.scenario] || pr.baseline); if (!sc) continue; const line = schedule.lines[k];
        for (const t of sc.trains) { const pts = t.points.map(pt => [track.stops.findIndex(s => s.stop_id === line.stops[pt.idx]) - track.startIdx, pt.eta_ts]).filter(q => q[0] >= 0 && q[0] <= track.endIdx - track.startIdx);
          if (pts.length >= 2) lg.trains.push({ trip_id: `${t.trip_id}#model`, train_id: t.trip_id, route_id: k.split("_")[0], points: pts, kind: t.hold_extra_sec > 0 || t.knock_on_sec >= 60 ? "sim-hold" : "sim" }); } } });
      const age = state.demo ? 0 : now - state.lbNow; const markers = [];
      state.tracks.forEach((track, li) => { for (const k of track.keys) { const lb = state.boards[k]; if (!lb) continue; for (const t of lb.trains) { const p = progressOnTrack(t, k, track, age); if (!p || p.idx < track.startIdx || p.idx > track.endIdx) continue; markers.push({ leg: li, stop: p.idx - track.startIdx, ts: now, color: stateColor(p.state), label: `${k.split("_")[0]} ${(t.train_id || t.trip_id).trim()}`, rows: [["state", p.state], ["lateness", lateTxt(t.effective_lateness_sec ?? t.lateness_sec)]] }); } } });
      const it = its[0]; const path = [];
      if (it) { path.push([0, state.tracks[0].fromIdx - state.tracks[0].startIdx, now], [0, state.tracks[0].fromIdx - state.tracks[0].startIdx, it.legs[0].board_ts], [0, state.tracks[0].toIdx - state.tracks[0].startIdx, it.legs[0].arrive_ts]);
        if (it.legs[1]) path.push([1, state.tracks[1].fromIdx - state.tracks[1].startIdx, it.legs[1].board_ts], [1, state.tracks[1].toIdx - state.tracks[1].startIdx, it.legs[1].arrive_ts]); }
      stringline(state.stringBox, { title: "This path over the next 45 minutes", subtitle: "dots: trains now; dashed: the feed's projection (red: held or stalled); dotted: the prediction engine (red: a held train, or one it holds back); the red dashed path is your recommended itinerary: wait, ride, change, ride", legs, now, horizonSec: 2700, backSec: 300, highlight: new Set(it ? it.legs.map(l => l.trip_id) : []), path, routeColor: r => ROUTE_COLORS[r] || null, rowH: 14, markers });
    }
  }
  function fallbackBoards() {
    const out = {}; let any = false;
    for (const k of involvedKeys) { const d = devByKey[k]; const snap = d && d.snapshot; if (!snap || !snap.live) continue; const line = schedule.lines[k]; const stops = line.stops.map((s, i) => ({ stop_id: s, name: line.names[i] }));
      out[k] = { route: k.split("_")[0], direction: k.split("_")[1], now: snap.now, stops, n_holding: 0, n_stalled: 0, n_feed_optimistic: 0, trains: snap.live.filter(t => t.started && t.points && t.points.length).map(t => ({ trip_id: t.trip_id, train_id: t.train_id, route: k.split("_")[0], points: t.points, next_idx: t.points[0][0], next_name: stops[t.points[0][0]] && stops[t.points[0][0]].name, eta_ts: t.points[0][1], lateness_sec: t.lateness, effective_lateness_sec: t.lateness, position: null, corroboration: "position_unknown", track_changed: t.track_changed, started: true })) };
      any = true; state.lbNow = state.lbNow || snap.now; }
    return any ? out : null;
  }
  function predictAll() {
    const now = nowRef(); state.pred = {}; state.predBoards = {}; state.anyHeld = false;
    for (const k of involvedKeys) { const lb = state.boards[k]; if (!lb) continue; const pr = predictBoard(lb, schedule.lines[k], cmodel, now); state.pred[k] = pr; if (pr.hold_persists) state.anyHeld = true;
      const sc = pr[state.scenario] || pr.baseline; const byTrip = new Map(sc.trains.map(t => [t.trip_id, t]));
      state.predBoards[k] = { ...lb, trains: lb.trains.map(t => { const q = byTrip.get(t.trip_id); return q && q.points.length ? { ...t, feed_points: t.points, points: q.points.map(pt => [pt.idx, pt.eta_ts]), pred: q } : t; }) }; }
    scen.style.display = state.anyHeld ? "" : "none";
  }
  function onBoards() { const now = nowRef(); predictAll(); for (const p of paths) p.live = pathTrips(state.predBoards, schedule, p, now)[0] || null; if (!pathId) selected = rank()[0]; drawPaths(); renderLive(); }
  drawPaths(); drawDetail();
  clientLive = createClientLive({ base: DATA, feedKeys: () => feedKeys,
    onUpdate: (board, sch, feeds) => { for (const k of involvedKeys) { const [r, d] = k.split("_"); if (sch.lines[k]) state.boards[k] = lineBoard(sch, lineSched[k] || [], feeds, r, d, board.now); } state.lbNow = board.now; state.alerts = board.alerts;
      statusEl.textContent = `live from ${feedKeys.length} MTA feed${feedKeys.length > 1 ? "s" : ""} · ${state.demo ? "recorded snapshot" : `polled ${hhmmss(board.now)} ET, next in ${Math.round((board.next_poll_ms || 30000) / 1000)} s (aligned to the feed's 30-second updates)`}`; onBoards(); },
    onError: e => { statusEl.textContent = `feeds unreachable from this browser (${e.message || e})`; if (!state.lbNow) { const fb = fallbackBoards(); if (fb) { state.boards = fb; statusEl.textContent += "; showing the pipeline's last line snapshots"; onBoards(); } } } });
  clientLive.start();
  liveTimer = setInterval(() => { tickDiagram(); tickTiles(); }, 1000);
}
