import { barChart, lineChart, heatmap, sparkline, stringline, fmt, seriesColor } from "./charts.js";

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
let liveTimer = null;
const hoursText = hs => hs && hs.length ? hs.map(x => `${String(x).padStart(2, "0")}:00`).join(", ") : "all hours";
const causeName = c => (c || "").replace(/_/g, " ");
const dateTime = iso => { try { return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }); } catch { return iso; } };
const pctChange = v => v == null ? "–" : `${v > 0 ? "+" : ""}${(v * 100).toFixed(0)}%`;

// ---------------------------------------------------------------- routing
const routes = { "": home, lines: lines, alerts: alerts, data: dataPage, station: station, live: live, plan: plan, routes: routesPage };
async function render() {
  const [section = "", arg] = location.hash.replace(/^#\/?/, "").split("/");
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
window.addEventListener("hashchange", () => { if (liveTimer) { clearInterval(liveTimer); liveTimer = null; } render(); });
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
}

// ---------------------------------------------------------------- data / about
async function dataPage(idx) {
  const st = await load("status.json");
  h("h1", null, "Data pipeline and sources", app);
  h("p", "secondary", "A GitHub Actions job polls the MTA GTFS-Realtime feeds for the monitored platforms and their upstream stops, derives observed arrivals, pulls Open Data context, re-runs the analyses and publishes this site.", app);
  const tiles = h("div", "tiles", null, app);
  tile(tiles, "Observed arrivals", fmt.compact(st.arrivals_total)); tile(tiles, "Days with data", st.days_with_data); tile(tiles, "Pipeline runs logged", (st.runs || []).length);
  tile(tiles, "Static GTFS", st.gtfs?.feed_version || "–", st.gtfs ? `${st.gtfs.stations} stations · ${st.gtfs.routes} routes` : "");
  const days = Object.keys(st.arrivals_per_day || {}).sort();
  if (days.length) { const c = h("div", "card", null, app); c.style.marginTop = "1rem"; barChart(c, { title: "Observed arrivals per day", categories: days.map(d => d.slice(5)), series: [{ name: "arrivals", values: days.map(d => st.arrivals_per_day[d]) }], labelEvery: Math.max(1, Math.ceil(days.length / 12)) }); }
  h("h2", null, "Recent runs", app);
  const wrap = h("div", "table-wrap card", null, app); const t = h("table", null, null, wrap); const tr = h("tr", null, null, h("thead", null, null, t));
  ["when", "kind", "polls", "arrivals", "errors", "notes"].forEach(x => h("th", null, x, tr)); const tb = h("tbody", null, null, t);
  (st.runs || []).slice().reverse().slice(0, 30).forEach(r => { const row = h("tr", null, null, tb); h("td", "small", dateTime(r.iso), row); h("td", null, r.kind, row); h("td", "num", r.polls ?? "–", row); h("td", "num", r.arrivals ?? "–", row); h("td", "num", r.errors ?? "–", row);
    h("td", "small", r.kind === "context" ? `ok: ${(r.ok || []).join(", ")}${Object.keys(r.failed || {}).length ? " · failed: " + Object.keys(r.failed).join(", ") : ""}` : (r.feeds || []).join(", "), row); });
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

async function live(idx) {
  const root = app;
  async function draw() {
    let d;
    try { await dataReady; const r = await fetch(DATA + "live.json", { cache: "no-store" }); if (!r.ok) throw new Error(String(r.status)); d = await r.json(); }
    catch (e) { root.replaceChildren(); h("h1", null, "Live status", root); const em = h("div", "empty", null, root); h("div", null, "No live snapshot is available yet.", em); h("div", "small muted", "The pipeline publishes data/live.json during each collection run; for continuous 30-second updates run `mta-insights serve` locally.", em); return; }
    if (!d.generated_ts) { root.replaceChildren(); h("div", "empty", "Live snapshot is starting…", root); return; }
    const now = Date.now() / 1000, age = now - d.generated_ts;
    root.replaceChildren();
    const head = h("div", "row between", null, root);
    h("h1", null, "Live status", head);
    const rf = h("div", "refresh small secondary", null, head);
    h("span", "age", `as of ${hhmm(d.generated_ts)} ET · ${ageText(age)} · ${d.source}`, rf);
    const btn = h("button", "icon-btn", "↻", rf); btn.title = "Refresh"; btn.addEventListener("click", draw);
    if (age > 900) h("p", "small", `This snapshot is ${ageText(age).replace(" ago", "")} old. The Pages site refreshes only while the hourly collector runs; run mta-insights serve for continuous updates.`, root).style.color = "var(--status-serious)";
    const tiles = h("div", "tiles", null, root);
    tile(tiles, "Trains in service", d.trains_total, `${d.trains_matched} matched to schedule${d.trains_scheduled_not_started ? ` · ${d.trains_scheduled_not_started} scheduled, not yet departed` : ""}`);
    tile(tiles, "Routes good", d.summary.good); tile(tiles, "Routes degraded", d.summary.degraded); tile(tiles, "Routes disrupted", d.summary.disrupted);
    tile(tiles, "Unplanned alerts", d.alerts.length);

    // Monitored stations: forecasts and downstream effects
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
      if ((s.arrivals || []).length) {
        const wrap = h("div", "table-wrap", null, card); wrap.style.marginTop = ".5rem";
        const tb = h("table", null, null, wrap); const tr = h("tr", null, null, h("thead", null, null, tb));
        ["route", "feed ETA", "model ETA", "range", "vs schedule", "now at", "late now"].forEach((x, i) => h("th", i >= 1 && i <= 4 ? "num" : "", x, tr));
        const body = h("tbody", null, null, tb);
        s.arrivals.slice(0, 8).forEach(a => { const row = h("tr", a.gap ? "gap-row" : "", null, body); const c0 = h("td", null, null, row); routeBullet(a.route_id, c0);
          h("td", "num eta", hhmm(a.feed_eta_ts), row); h("td", "num eta", hhmm(a.model_eta_ts), row); h("td", "num eta small", `${hhmm(a.eta_lo_ts)}–${hhmm(a.eta_hi_ts)}`, row);
          h("td", "num", lateTxt(a.model_lateness_sec), row); h("td", "small", a.started === false ? "not departed" : (a.now_at_stop_name || "–"), row); h("td", "num", a.started === false ? "–" : lateTxt(a.now_lateness_sec), row); });
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
    h("p", "tiny muted", "Feed ETAs come from the MTA GTFS-Realtime trip updates. Model ETAs add the look-back calibration (how much ETAs at this lead time slipped historically at this platform) and the historical effect of active alerts; the range is the p10–p90 of past ETA error. Route status: disrupted = a Delays/Suspended alert, a gap ≥ 2.5× the scheduled headway or median lateness ≥ 8 min; degraded = any unplanned alert, gap ≥ 1.6× or lateness ≥ 4 min.", root);
  }
  await draw();
  liveTimer = setInterval(draw, 60000);
}


// ---------------------------------------------------------------- plan (trip time planner)
const minTxt = s => s == null ? "–" : `${(s / 60).toFixed(0)} min`;
async function plan(idx, journeyId) {
  const root = app;
  async function draw() {
    let d;
    try { await dataReady; const r = await fetch(DATA + "live.json", { cache: "no-store" }); if (!r.ok) throw new Error(String(r.status)); d = await r.json(); }
    catch (e) { root.replaceChildren(); h("h1", null, "Trip planner", root); h("div", "empty", "No live snapshot yet. The planner needs data/live.json (published by the collector, or served by mta-insights serve).", root); return; }
    const journeys = d.journeys || [];
    root.replaceChildren();
    const head = h("div", "row between", null, root);
    h("h1", null, "Trip planner: how long will it take right now?", head);
    const rf = h("div", "refresh small secondary", null, head);
    h("span", "age", `as of ${hhmm(d.generated_ts)} ET · ${ageText(Date.now() / 1000 - d.generated_ts)} · ${d.source}`, rf);
    const btn = h("button", "icon-btn", "↻", rf); btn.title = "Refresh"; btn.addEventListener("click", draw);
    if (!journeys.length) { h("div", "empty", "No journeys configured. Add them under \"journeys\" in pipeline/targets.json.", root); return; }
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
