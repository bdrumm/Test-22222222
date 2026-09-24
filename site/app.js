import { barChart, lineChart, heatmap, sparkline, fmt, seriesColor } from "./charts.js";

const app = document.getElementById("app");
const DATA = "data/";
const cache = new Map();
async function load(path) {
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
const hoursText = hs => hs && hs.length ? hs.map(x => `${String(x).padStart(2, "0")}:00`).join(", ") : "all hours";
const causeName = c => (c || "").replace(/_/g, " ");
const dateTime = iso => { try { return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }); } catch { return iso; } };
const pctChange = v => v == null ? "–" : `${v > 0 ? "+" : ""}${(v * 100).toFixed(0)}%`;

// ---------------------------------------------------------------- routing
const routes = { "": home, lines: lines, alerts: alerts, data: dataPage, station: station };
async function render() {
  const [section = "", arg] = location.hash.replace(/^#\/?/, "").split("/");
  document.querySelectorAll("[data-nav]").forEach(a => a.classList.toggle("active", a.dataset.nav === (section || "home")));
  app.replaceChildren(); h("p", "muted", "Loading…", app);
  try {
    const idx = await load("index.json");
    document.getElementById("generated").textContent = `updated ${dateTime(idx.generated_at)}${idx.mode === "synthetic" ? " · synthetic preview" : ""}`;
    app.replaceChildren();
    await (routes[section] || home)(idx, arg);
    window.scrollTo(0, 0);
  } catch (err) {
    app.replaceChildren(); const e = h("div", "empty", null, app); h("div", null, "Could not load the site data.", e); h("div", "small muted", String(err.message || err), e);
  }
}
window.addEventListener("hashchange", render);
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
    badge(`${t.severity.label} · ${t.severity.score}`, sevClass(t.severity.label), head);
    const meter = h("div", "meter", null, card); h("span", null, null, meter).style.width = `${Math.min(100, t.severity.score)}%`;
    h("p", "small", t.verdict, card).style.marginTop = ".6rem";
    const kv = h("div", "small secondary", null, card);
    kv.append("Focus hours: "); const hs = h("span", "hours", null, kv); (t.focus_hours || []).forEach(x => h("span", null, String(x).padStart(2, "0"), hs)); if (!t.focus_hours?.length) kv.append("all hours");
    if (t.top_location) h("div", "small secondary", `Where: ${causeName(t.top_location.cause)}`, card);
    if (t.top_cause) h("div", "small secondary", `Why: ${causeName(t.top_cause.cause)} (support ${t.top_cause.score.toFixed(2)})`, card);
    if (t.impact) h("div", "small secondary", `Impact: ${fmt.num(t.impact.passenger_hours_per_day)} passenger-hours/day`, card);
    h("div", "tiny muted", `${fmt.num(t.arrival_count)} arrivals · ${t.coverage_windows?.span_hours ?? "?"} h of coverage`, card);
    link(`#/station/${t.id}`, "Open report →", card, "small").style.display = "inline-block";
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
  const sev = tile(tiles, "Severity (0-100)", r.severity.score); badge(r.severity.label, sevClass(r.severity.label), sev);
  tile(tiles, "Extra journey time", r.impact ? `${fmt.num(r.impact.passenger_hours_per_day)} pax-h/day` : "–", r.impact ? `${fmt.num1(r.impact.extra_wait_min_per_rider)} min per rider · ${fmt.compact(r.impact.riders_per_day_exposed)} riders/day` : "");
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
  h("h2", null, `What changed${r.focus_mode === "detected" ? " (focus hours)" : ""}`, app);
  comparisonTable(r.comparisons, app);
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
