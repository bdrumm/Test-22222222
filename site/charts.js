// Small SVG chart kit following the data-viz method: thin marks, hairline grid, 2px surface gaps,
// legend for >=2 series, hover tooltips, and a table-view twin for every chart.
const SVG = "http://www.w3.org/2000/svg";
const SERIES = ["--series-1", "--series-2", "--series-3", "--series-4", "--series-5", "--series-6", "--series-7", "--series-8"];
const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
export const seriesColor = (i) => cssVar(SERIES[i % SERIES.length]);

function el(tag, attrs = {}, parent = null) {
  const e = document.createElementNS(SVG, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (parent) parent.appendChild(e);
  return e;
}
function html(tag, cls, text, parent) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  if (parent) parent.appendChild(e);
  return e;
}
const fmtNum = (v, d = 0) => (v == null || Number.isNaN(v)) ? "–" : Number(v).toLocaleString(undefined, { maximumFractionDigits: d, minimumFractionDigits: d });
export const fmt = {
  num: (v) => fmtNum(v, 0), num1: (v) => fmtNum(v, 1), pct: (v) => v == null ? "–" : `${fmtNum(v * 100, 0)}%`,
  min: (v) => v == null ? "–" : `${fmtNum(v / 60, 1)} min`, sec: (v) => v == null ? "–" : `${fmtNum(v, 0)} s`,
  compact: (v) => v == null ? "–" : Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 }).format(v),
};
function niceTicks(max, n = 4) {
  if (!(max > 0)) return [0, 1];
  const raw = max / n, mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 2.5, 5, 10].map(m => m * mag).find(s => s >= raw) || mag * 10;
  const ticks = []; for (let v = 0; v <= max + 1e-9; v += step) ticks.push(v);
  if (ticks[ticks.length - 1] < max) ticks.push(ticks[ticks.length - 1] + step);
  return ticks;
}

class Frame {
  constructor(container, { title, subtitle, series, kind = "rect", height = 240, dense = false }) {
    this.root = html("figure", "chart", null, container);
    this.root.style.margin = "0";
    if (title) html("div", "title", title, this.root);
    if (subtitle) html("div", "subtitle", subtitle, this.root);
    if (series && series.length > 1) {
      const lg = html("div", "legend", null, this.root);
      series.forEach((s, i) => {
        const item = html("span", null, null, lg);
        const key = html("span", "key" + (kind === "line" ? " line" : ""), null, item);
        key.style.background = s.color || seriesColor(i);
        item.appendChild(document.createTextNode(s.name));
      });
    }
    this.tip = html("div", "tooltip", null, this.root);
    const tg = html("div", "toggle", null, this.root);
    const btn = html("button", null, "Table", tg);
    btn.type = "button";
    btn.addEventListener("click", () => { this.root.classList.toggle("show-table"); btn.textContent = this.root.classList.contains("show-table") ? "Chart" : "Table"; });
    this.svg = el("svg", { viewBox: `0 0 720 ${height}`, role: "img" }, this.root);
    this.height = height; this.width = 720;
    if (dense) { this.root.classList.add("dense"); this.svg.style.minWidth = "640px"; }
    this.tableWrap = html("div", "table-view table-wrap", null, this.root);
  }
  table(headers, rows) {
    const t = html("table", null, null, this.tableWrap);
    const tr = html("tr", null, null, html("thead", null, null, t));
    headers.forEach((h, i) => html("th", i ? "num" : "", h, tr));
    const tb = html("tbody", null, null, t);
    rows.forEach(r => { const tr2 = html("tr", null, null, tb); r.forEach((c, i) => html("td", i ? "num" : "", c, tr2)); });
  }
  showTip(x, y, head, rows) {
    this.tip.replaceChildren();
    html("div", "t-head", head, this.tip);
    rows.forEach(([name, value, color]) => {
      const r = html("div", "t-row", null, this.tip);
      const l = html("span", null, null, r);
      if (color) { const k = html("span", "lk", null, l); k.style.background = color; }
      l.appendChild(document.createTextNode(name));
      html("b", null, value, r);
    });
    this.tip.style.display = "block";
    const box = this.root.getBoundingClientRect(), tw = this.tip.offsetWidth;
    let left = x - box.left + 12; if (left + tw > box.width) left = x - box.left - tw - 12;
    this.tip.style.left = `${Math.max(0, left)}px`; this.tip.style.top = `${y - box.top + 12}px`;
  }
  hideTip() { this.tip.style.display = "none"; }
}

// Grouped or stacked columns. categories: string[]; series: [{name, values:number[], color?}]
export function barChart(container, { title, subtitle, categories, series, stacked = false, format = fmt.num, height = 240, labelEvery = 1, yLabel, refLines = [] }) {
  const f = new Frame(container, { title, subtitle, series, height });
  const m = { l: 44, r: 12, t: 10, b: 34 }, W = f.width - m.l - m.r, H = height - m.t - m.b;
  const n = categories.length, k = series.length;
  const totals = categories.map((_, i) => stacked ? series.reduce((a, s) => a + (s.values[i] || 0), 0) : Math.max(...series.map(s => s.values[i] || 0)));
  const ticks = niceTicks(Math.max(...totals, ...refLines.map(r => r.value || 0), 0)), yMax = ticks[ticks.length - 1] || 1;
  const y = v => m.t + H - (v / yMax) * H;
  ticks.forEach(t => { el("line", { x1: m.l, x2: m.l + W, y1: y(t), y2: y(t), class: "grid-line" }, f.svg);
    el("text", { x: m.l - 6, y: y(t) + 4, "text-anchor": "end", class: "axis-text" }, f.svg).textContent = format === fmt.pct ? fmt.pct(t) : (format === fmt.min ? `${fmtNum(t / 60, 1)}m` : fmt.compact(t)); });
  el("line", { x1: m.l, x2: m.l + W, y1: y(0), y2: y(0), class: "axis-line" }, f.svg);
  const slot = W / n, groupW = stacked ? Math.min(24, slot * 0.7) : Math.min(24 * k, slot * 0.8), barW = stacked ? groupW : Math.min(24, groupW / k - 2);
  categories.forEach((c, i) => {
    const x0 = m.l + slot * i + slot / 2;
    let acc = 0;
    series.forEach((s, j) => {
      const v = s.values[i] || 0; if (v <= 0 && !stacked) return;
      const color = s.color || seriesColor(j);
      let x, yTop, h;
      if (stacked) { x = x0 - barW / 2; const y1 = y(acc), y2 = y(acc + v); yTop = y2; h = Math.max(0, y1 - y2 - (acc > 0 ? 2 : 0)); acc += v; }
      else { x = x0 - groupW / 2 + j * (barW + 2); yTop = y(v); h = y(0) - yTop; }
      if (h <= 0) return;
      const r = el("rect", { x, y: yTop, width: barW, height: h, fill: color, class: "bar", rx: stacked && acc !== v + (acc - v) ? 0 : 4 }, f.svg);
      if (!stacked || j === k - 1) r.setAttribute("rx", "4"); else r.setAttribute("rx", "0");
      const hit = el("rect", { x: x - 2, y: Math.min(yTop, m.t), width: barW + 4, height: H + 4, class: "hit" }, f.svg);
      hit.addEventListener("pointermove", ev => f.showTip(ev.clientX, ev.clientY, c, series.map((ss, jj) => [ss.name, format(ss.values[i]), ss.color || seriesColor(jj)])));
      hit.addEventListener("pointerleave", () => f.hideTip());
    });
    if (i % labelEvery === 0) el("text", { x: x0, y: height - m.b + 16, "text-anchor": "middle", class: "axis-text" }, f.svg).textContent = c;
  });
  if (yLabel) el("text", { x: m.l, y: m.t - 2, class: "axis-text" }, f.svg).textContent = yLabel;
  refLines.forEach(r => { if (!(r.value > 0)) return; el("line", { x1: m.l, x2: m.l + W, y1: y(r.value), y2: y(r.value), stroke: cssVar("--text-secondary"), "stroke-width": 1.5 }, f.svg);
    if (r.label) el("text", { x: m.l + W - 4, y: y(r.value) - 4, "text-anchor": "end", class: "dlabel" }, f.svg).textContent = r.label; });
  f.table(["", ...series.map(s => s.name)], categories.map((c, i) => [c, ...series.map(s => format(s.values[i]))]));
  return f.root;
}

// Multi-series line chart with crosshair tooltip. x: string[] labels; series: [{name, values, color?}]
export function lineChart(container, { title, subtitle, x, series, format = fmt.num, height = 220, bands = [], labelEvery, yMin }) {
  const f = new Frame(container, { title, subtitle, series, kind: "line", height });
  const m = { l: 48, r: series.length <= 4 ? 52 : 14, t: 10, b: 30 }, W = f.width - m.l - m.r, H = height - m.t - m.b;
  const all = series.flatMap(s => s.values).filter(v => v != null && Number.isFinite(v));
  const lo = yMin != null ? yMin : Math.min(0, ...all), ticks = niceTicks(Math.max(...all, 0) - lo), yMax = lo + (ticks[ticks.length - 1] || 1);
  const n = x.length, xp = i => m.l + (n > 1 ? (i / (n - 1)) * W : W / 2), yp = v => m.t + H - ((v - lo) / (yMax - lo)) * H;
  bands.forEach(b => { const x1 = xp(b.from), x2 = xp(b.to); const r = el("rect", { x: x1, y: m.t, width: Math.max(0, x2 - x1), height: H, fill: cssVar("--seq-100"), opacity: .55 }, f.svg);
    if (b.label) el("text", { x: x1 + 4, y: m.t + 12, class: "axis-text" }, f.svg).textContent = b.label; });
  ticks.forEach(t => { const v = lo + t; el("line", { x1: m.l, x2: m.l + W, y1: yp(v), y2: yp(v), class: "grid-line" }, f.svg);
    el("text", { x: m.l - 6, y: yp(v) + 4, "text-anchor": "end", class: "axis-text" }, f.svg).textContent = format === fmt.pct ? fmt.pct(v) : (format === fmt.min ? fmtNum(v / 60, 1) : fmt.compact(v)); });
  el("line", { x1: m.l, x2: m.l + W, y1: yp(lo), y2: yp(lo), class: "axis-line" }, f.svg);
  const every = labelEvery || Math.max(1, Math.ceil(n / 8));
  const lastLabeled = Math.floor((n - 1) / every) * every;
  x.forEach((lab, i) => { if (i % every === 0 || (i === n - 1 && i - lastLabeled >= every / 2)) el("text", { x: xp(i), y: height - m.b + 16, "text-anchor": "middle", class: "axis-text" }, f.svg).textContent = lab; });
  series.forEach((s, j) => {
    const color = s.color || seriesColor(j);
    let d = "", pen = false;
    s.values.forEach((v, i) => { if (v == null || !Number.isFinite(v)) { pen = false; return; } d += `${pen ? "L" : "M"}${xp(i).toFixed(1)},${yp(v).toFixed(1)} `; pen = true; });
    el("path", { d, fill: "none", stroke: color, "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }, f.svg);
    const last = [...s.values.keys()].reverse().find(i => s.values[i] != null && Number.isFinite(s.values[i]));
    if (last != null) { el("circle", { cx: xp(last), cy: yp(s.values[last]), r: 4, fill: color, stroke: cssVar("--surface-1"), "stroke-width": 2 }, f.svg);
      if (series.length <= 4) el("text", { x: xp(last) + 6, y: yp(s.values[last]) + 4, class: "dlabel" }, f.svg).textContent = format(s.values[last]); }
  });
  const cross = el("line", { y1: m.t, y2: m.t + H, class: "crosshair", style: "display:none" }, f.svg);
  const hit = el("rect", { x: m.l, y: m.t, width: W, height: H, class: "hit" }, f.svg);
  hit.addEventListener("pointermove", ev => {
    const box = f.svg.getBoundingClientRect(), px = (ev.clientX - box.left) * (f.width / box.width);
    const i = Math.max(0, Math.min(n - 1, Math.round(((px - m.l) / W) * (n - 1))));
    cross.setAttribute("x1", xp(i)); cross.setAttribute("x2", xp(i)); cross.style.display = "";
    f.showTip(ev.clientX, ev.clientY, x[i], series.map((s, j) => [s.name, format(s.values[i]), s.color || seriesColor(j)]));
  });
  hit.addEventListener("pointerleave", () => { cross.style.display = "none"; f.hideTip(); });
  f.table(["", ...series.map(s => s.name)], x.map((lab, i) => [lab, ...series.map(s => format(s.values[i]))]));
  return f.root;
}

// Heatmap: rows x cols with a sequential blue ramp.
export function heatmap(container, { title, subtitle, rows, cols, values, format = fmt.pct, colLabelEvery = 2, rowLabelEvery = 1 }) {
  const cellH = 14, m = { l: 74, r: 8, t: 8, b: 24 }, height = m.t + rows.length * cellH + m.b;
  const f = new Frame(container, { title, subtitle, series: null, height, dense: true });
  const W = f.width - m.l - m.r, cw = W / cols.length;
  const ramp = ["--seq-100", "--seq-200", "--seq-300", "--seq-400", "--seq-500", "--seq-600", "--seq-700"].map(cssVar);
  const flat = values.flat().filter(v => v != null && Number.isFinite(v)), vmax = Math.max(...flat, 1e-9);
  rows.forEach((r, ri) => {
    if (ri % rowLabelEvery === 0) el("text", { x: m.l - 6, y: m.t + ri * cellH + 11, "text-anchor": "end", class: "axis-text" }, f.svg).textContent = r;
    cols.forEach((c, ci) => {
      const v = values[ri][ci];
      const color = v == null ? cssVar("--grid") : ramp[Math.min(6, Math.floor((v / vmax) * 6.999))];
      const rect = el("rect", { x: m.l + ci * cw + 1, y: m.t + ri * cellH + 1, width: Math.max(1, cw - 2), height: cellH - 2, fill: color, class: "cell", rx: 2 }, f.svg);
      rect.addEventListener("pointermove", ev => f.showTip(ev.clientX, ev.clientY, `${r} · ${c}`, [["value", v == null ? "no data" : format(v)]]));
      rect.addEventListener("pointerleave", () => f.hideTip());
    });
  });
  cols.forEach((c, ci) => { if (ci % colLabelEvery === 0) el("text", { x: m.l + ci * cw + cw / 2, y: height - 8, "text-anchor": "middle", class: "axis-text" }, f.svg).textContent = c; });
  f.table(["", ...cols], rows.map((r, ri) => [r, ...cols.map((_, ci) => values[ri][ci] == null ? "–" : format(values[ri][ci]))]));
  return f.root;
}

export function sparkline(container, values, { color, width = 120, height = 28 } = {}) {
  const svg = el("svg", { viewBox: `0 0 ${width} ${height}` }, container);
  svg.style.width = `${width}px`; svg.style.height = `${height}px`;
  const vals = values.filter(v => v != null && Number.isFinite(v)); if (vals.length < 2) return svg;
  const lo = Math.min(...vals), hi = Math.max(...vals), n = values.length;
  const xp = i => (i / (n - 1)) * (width - 4) + 2, yp = v => height - 3 - ((v - lo) / ((hi - lo) || 1)) * (height - 6);
  let d = ""; values.forEach((v, i) => { if (v == null) return; d += `${d ? "L" : "M"}${xp(i).toFixed(1)},${yp(v).toFixed(1)} `; });
  el("path", { d, fill: "none", stroke: color || cssVar("--de-emphasis"), "stroke-width": 2, "stroke-linecap": "round" }, svg);
  const last = n - 1; if (values[last] != null) el("circle", { cx: xp(last), cy: yp(values[last]), r: 3, fill: color || cssVar("--series-1") }, svg);
  return svg;
}


// Time-distance ("stringline") chart: stops on the y axis, time on the x axis, one line per train.
// legs: [{stops:[{name}], trains:[{trip_id, route_id, points:[[stopIdx, ts]], lateness_sec}]}]
// highlight: Set of trip_ids to emphasise; path: [[legIdx, stopIdx, ts], ...] the rider's own itinerary.
export function stringline(container, { title, subtitle, legs, now, horizonSec = 3600, backSec = 300, highlight = new Set(), path = [], routeColor = () => null, rowH = 18 }) {
  const stops = []; const legOffsets = [];
  legs.forEach((lg, li) => { legOffsets.push(stops.length); lg.stops.forEach((st, i) => {
    if (li > 0 && i === 0) { const prev = stops[stops.length - 1]; stops[stops.length - 1] = { ...prev, name: prev.name === st.name ? st.name : `${prev.name} / ${st.name}` }; legOffsets[li] = stops.length - 1; return; }
    stops.push({ name: st.name, leg: li }); }); });
  const m = { l: 150, r: 16, t: 16, b: 28 }, height = m.t + Math.max(1, stops.length - 1) * rowH + m.b;
  const f = new Frame(container, { title, subtitle, series: null, height, dense: true });
  const W = f.width - m.l - m.r, H = height - m.t - m.b;
  const t0 = now - backSec, t1 = now + horizonSec;
  const xp = ts => m.l + ((ts - t0) / (t1 - t0)) * W, yp = i => m.t + (stops.length > 1 ? (i / (stops.length - 1)) * H : H / 2);
  const gIndex = (li, si) => legOffsets[li] + si;
  stops.forEach((st, i) => { el("line", { x1: m.l, x2: m.l + W, y1: yp(i), y2: yp(i), class: "grid-line" }, f.svg);
    const label = st.name.length > 22 ? st.name.slice(0, 21) + "…" : st.name;
    el("text", { x: m.l - 8, y: yp(i) + 4, "text-anchor": "end", class: "axis-text" }, f.svg).textContent = label; });
  const tick = (t1 - t0) > 5400 ? 1800 : 600;
  for (let t = Math.ceil(t0 / tick) * tick; t <= t1; t += tick) { el("line", { x1: xp(t), x2: xp(t), y1: m.t, y2: m.t + H, class: "grid-line" }, f.svg);
    el("text", { x: xp(t), y: height - 8, "text-anchor": "middle", class: "axis-text" }, f.svg).textContent = new Date(t * 1000).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", timeZone: "America/New_York" }); }
  el("line", { x1: xp(now), x2: xp(now), y1: m.t, y2: m.t + H, stroke: cssVar("--text-secondary"), "stroke-width": 1.5 }, f.svg);
  el("text", { x: xp(now) + 4, y: m.t + 10, class: "dlabel" }, f.svg).textContent = "now";
  const rows = [];
  const lateColor = l => l == null ? cssVar("--de-emphasis") : l >= 300 ? cssVar("--status-critical") : l >= 120 ? cssVar("--status-warning") : cssVar("--status-good");
  legs.forEach((lg, li) => lg.trains.forEach(tr => {
    const pts = tr.points.filter(([, ts]) => ts >= t0 && ts <= t1).map(([si, ts]) => [xp(ts), yp(gIndex(li, si))]);
    if (pts.length < 2) return;
    const hi = highlight.has(tr.trip_id);
    const kind = tr.kind || "feed";
    const isSim = kind === "sim" || kind === "sim-hold";
    const color = kind === "sched" ? cssVar("--grid") : kind === "actual" ? lateColor(tr.lateness_sec) : kind === "sim-hold" ? cssVar("--status-critical") : (routeColor(tr.route_id) || seriesColor(li));
    const baseW = kind === "sched" ? 1 : kind === "actual" ? 2 : isSim ? 1.4 : 1.5;
    const d = pts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(" ");
    const pathEl = el("path", { d, fill: "none", stroke: hi ? color : (kind === "feed" && highlight.size ? cssVar("--de-emphasis") : color),
      "stroke-width": hi ? 3 : baseW, "stroke-linejoin": "round", "stroke-linecap": "round",
      opacity: hi ? 1 : (kind === "sched" ? 0.7 : isSim ? 0.85 : 0.9), ...(kind === "live" ? { "stroke-dasharray": "5 4" } : isSim ? { "stroke-dasharray": "1.5 3.5" } : {}) }, f.svg);
    const hit = el("path", { d, fill: "none", stroke: "transparent", "stroke-width": 14 }, f.svg);
    const late = tr.lateness_sec == null ? "–" : `${tr.lateness_sec >= 0 ? "+" : "−"}${Math.abs(tr.lateness_sec / 60).toFixed(0)} min`;
    if (kind !== "sched") {
      const kindName = kind === "actual" ? "observed" : kind === "sim" ? "simulated" : kind === "sim-hold" ? "if the hold persists" : "projected";
      hit.addEventListener("pointermove", ev => { pathEl.setAttribute("stroke-width", "4"); f.showTip(ev.clientX, ev.clientY, `${tr.route_id || ""} ${kindName} ${tr.train_id || tr.trip_id}`, [["lateness", late], ["stops", String(pts.length)]]); });
      hit.addEventListener("pointerleave", () => { pathEl.setAttribute("stroke-width", hi ? "3" : String(baseW)); f.hideTip(); });
      rows.push([`${tr.route_id || ""} ${tr.train_id || tr.trip_id}`, kindName, late, String(pts.length)]);
    }
  }));
  if (path.length >= 2) {
    const d = path.map(([li, si, ts], i) => `${i ? "L" : "M"}${xp(ts).toFixed(1)},${yp(gIndex(li, si)).toFixed(1)}`).join(" ");
    el("path", { d, fill: "none", stroke: cssVar("--series-8"), "stroke-width": 2.5, "stroke-dasharray": "6 4", "stroke-linejoin": "round" }, f.svg);
    path.forEach(([li, si, ts]) => el("circle", { cx: xp(ts), cy: yp(gIndex(li, si)), r: 4, fill: cssVar("--series-8"), stroke: cssVar("--surface-1"), "stroke-width": 2 }, f.svg));
  }
  f.table(["train", "kind", "lateness", "stops"], rows);
  return f.root;
}
