// Browser-side live board straight from the MTA GTFS-Realtime feeds.
//
// The MTA endpoint allows cross-origin requests, so the published site can poll the feeds
// itself every 30 seconds instead of waiting for the hourly pipeline snapshot. The build
// ships data/client_schedule.json (today's scheduled arrivals at the monitored platforms,
// each line's stop sequence with scheduled running times, feed URLs and the server's
// constants) so the browser can compute lateness, holds, stalls, gaps and a "hold persists"
// scenario with the same rules the Python side uses.
//
// No dependency: a minimal protobuf wire decoder covers the GTFS-RT subset we read
// (trip updates, vehicle positions, feed timestamp) plus the NYCT extension fields
// (train id, assignment, scheduled/actual track).

const TD = new TextDecoder();

function readVarint(buf, pos) {
  // Values are accumulated with multiplication so timestamps (> 2^31) stay exact; only
  // values below 2^53 are representable, which covers every field we read.
  let value = 0, mul = 1, b;
  do { b = buf[pos++]; value += (b & 0x7f) * mul; mul *= 128; } while (b & 0x80);
  return [value, pos];
}

// Decode one message body into [field, value] pairs. Length-delimited values are returned
// as [start, end] ranges into the same buffer; varints as numbers; 32-bit as float; 64-bit skipped.
function decodeFields(buf, start, end) {
  const out = []; let pos = start;
  while (pos < end) {
    let key; [key, pos] = readVarint(buf, pos);
    const field = Math.floor(key / 8), wt = key & 7;
    if (wt === 0) { let v; [v, pos] = readVarint(buf, pos); out.push([field, v]); }
    else if (wt === 1) { out.push([field, null]); pos += 8; }
    else if (wt === 2) { let len; [len, pos] = readVarint(buf, pos); out.push([field, [pos, pos + len]]); pos += len; }
    else if (wt === 5) { out.push([field, new DataView(buf.buffer, buf.byteOffset + pos, 4).getFloat32(0, true)]); pos += 4; }
    else throw new Error(`unsupported protobuf wire type ${wt}`);
  }
  return out;
}
const str = (buf, r) => TD.decode(buf.subarray(r[0], r[1]));
const STATUS = ["INCOMING_AT", "STOPPED_AT", "IN_TRANSIT_TO"];

function parseTrip(buf, r) {
  const t = { trip_id: null, start_date: null, route_id: null, train_id: null, is_assigned: null };
  for (const [f, v] of decodeFields(buf, r[0], r[1])) {
    if (f === 1) t.trip_id = str(buf, v); else if (f === 3) t.start_date = str(buf, v); else if (f === 5) t.route_id = str(buf, v);
    else if (f === 1001 && Array.isArray(v)) for (const [ef, ev] of decodeFields(buf, v[0], v[1])) { if (ef === 1) t.train_id = str(buf, ev); else if (ef === 2) t.is_assigned = !!ev; }
  }
  return t;
}
function parseTripUpdate(buf, r) {
  const tu = { trip: null, stops: [], timestamp: null };
  for (const [f, v] of decodeFields(buf, r[0], r[1])) {
    if (f === 1) tu.trip = parseTrip(buf, v);
    else if (f === 4) tu.timestamp = v;
    else if (f === 2) {
      const s = { stop_id: null, arrival: null, departure: null, sched_track: null, actual_track: null };
      for (const [sf, sv] of decodeFields(buf, v[0], v[1])) {
        if (sf === 4) s.stop_id = str(buf, sv);
        else if (sf === 2 || sf === 3) { for (const [ef, ev] of decodeFields(buf, sv[0], sv[1])) if (ef === 2) { if (sf === 2) s.arrival = ev; else s.departure = ev; } }
        else if (sf === 1001) for (const [ef, ev] of decodeFields(buf, sv[0], sv[1])) { if (ef === 1) s.sched_track = str(buf, ev); else if (ef === 2) s.actual_track = str(buf, ev); }
      }
      tu.stops.push(s);
    }
  }
  return tu;
}
function parseVehicle(buf, r) {
  const v = { trip: null, status: null, stop_id: null, timestamp: null };
  for (const [f, val] of decodeFields(buf, r[0], r[1])) {
    if (f === 1) v.trip = parseTrip(buf, val); else if (f === 4) v.status = STATUS[val] || String(val); else if (f === 5) v.timestamp = val; else if (f === 7) v.stop_id = str(buf, val);
  }
  return v;
}

/** Parse a GTFS-RT FeedMessage (Uint8Array) into {timestamp, trips:[{trip, stops}], vehicles:[...]} */
export function parseFeed(bytes) {
  const buf = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
  const feed = { timestamp: null, trips: [], vehicles: [] };
  for (const [f, v] of decodeFields(buf, 0, buf.length)) {
    if (f === 1) { for (const [hf, hv] of decodeFields(buf, v[0], v[1])) if (hf === 3) feed.timestamp = hv; }
    else if (f === 2) for (const [ef, ev] of decodeFields(buf, v[0], v[1])) { if (ef === 3) feed.trips.push(parseTripUpdate(buf, ev)); else if (ef === 4) feed.vehicles.push(parseVehicle(buf, ev)); }
  }
  return feed;
}

// ---------------------------------------------------------------- board computation
export const tripSuffix = id => { const p = id.split("_"); return p.length >= 3 ? p.slice(-2).join("_") : id; };
// the suffix without its path code: some feeds (L, some G and 7 trips) publish ids like 020300_L..N
export const tripStem = id => { const m = /^(\d+_[^.]+\.\.?[NS])/.exec(tripSuffix(id)); return m ? m[1] : tripSuffix(id); };
const median = a => { if (!a.length) return null; const s = [...a].sort((x, y) => x - y); return s[Math.floor(s.length / 2)]; };

const NEAREST_TOL_SEC = 900;   // same tolerance as the offline matcher's nearest-by-route fallback
// -> [scheduled ts, method] : trip id, its stem (ids without a path code), or the nearest scheduled arrival of the
// same route (trips running on a supplement schedule whose realtime origin time differs from the static one)
function matchSched(sched, tripId, route, eta) {
  const suf = tripSuffix(tripId), stem = tripStem(tripId); let best = null, bestStem = null, near = null;
  for (const [s, r, ts] of sched) {
    if (r !== route || Math.abs(ts - eta) >= 3 * 3600) continue;
    if (s === suf) { if (best == null || Math.abs(ts - eta) < Math.abs(best - eta)) best = ts; }
    else if (tripStem(s) === stem) { if (bestStem == null || Math.abs(ts - eta) < Math.abs(bestStem - eta)) bestStem = ts; }
    if (Math.abs(ts - eta) <= NEAREST_TOL_SEC && (near == null || Math.abs(ts - eta) < Math.abs(near - eta))) near = ts;
  }
  return best != null ? [best, "trip_id"] : bestStem != null ? [bestStem, "trip_stem"] : near != null ? [near, "nearest"] : [null, null];
}
function schedHeadway(sched, route, now) {
  const ts = sched.filter(([, r, t]) => r === route && t >= now - 3600 && t <= now + 3600).map(x => x[2]);
  const d = []; for (let i = 1; i < ts.length; i++) d.push(ts[i] - ts[i - 1]);
  return median(d);
}

// Where the train is, whether it is holding or stalled, and how late its position proves it to be.
function describePosition(veh, line, targetStop, schedTarget, now, C) {
  const since = veh.timestamp ? Math.max(0, now - veh.timestamp) : null;
  const stops = line ? line.stops : [], names = line ? line.names : [];
  const j = stops.indexOf(veh.stop_id), k = stops.indexOf(targetStop);
  let holding = false, stalled = false, expectedRun = null, positionLateness = null;
  if (veh.status === "STOPPED_AT") holding = since != null && since >= C.hold_sec && j !== 0;   // waiting at the origin terminal is not a hold
  else if (since != null && j > 0 && line.run_sec[j - 1] != null) { expectedRun = line.run_sec[j - 1]; stalled = since > expectedRun + C.stall_slack_sec; }
  if (schedTarget != null && j >= 0 && k >= 0 && j <= k) {
    // scheduled time at the position stop ~ scheduled time at the target minus the canonical running time in between
    let run = 0, ok = true; for (let q = j; q < k; q++) { if (line.run_sec[q] == null) { ok = false; break; } run += line.run_sec[q]; }
    if (ok) { const remaining = veh.status === "STOPPED_AT" ? 0 : (expectedRun != null ? Math.max(0, expectedRun - since) : 0); positionLateness = now + remaining - (schedTarget - run); }
  }
  return { status: veh.status, stop_id: veh.stop_id, stop_name: j >= 0 ? names[j] : veh.stop_id, since_sec: since, holding, stalled, at_origin: j === 0, expected_run_sec: expectedRun, position_lateness_sec: positionLateness };
}

/** Build the board for every monitored platform from parsed feeds ({feedKey: parsedFeed}). */
export function computeBoard(schedule, feeds, now) {
  const C = { hold_sec: 150, stall_slack_sec: 120, past_slack_sec: 90, gap_ratio: 1.5, bunching_ratio: 0.5, hold_extra_sec: 600, min_headway_sec: 90, ...(schedule.constants || {}) };
  const trips = new Map(), vehicles = new Map();
  for (const [key, fd] of Object.entries(feeds)) {
    for (const tu of fd.trips) if (tu.trip && tu.trip.trip_id) trips.set(`${tu.trip.start_date || ""}|${tu.trip.trip_id}`, { ...tu, feed: key });
    for (const v of fd.vehicles) if (v.trip && v.trip.trip_id) vehicles.set(`${v.trip.start_date || ""}|${v.trip.trip_id}`, v);
  }
  const summary = { trips: trips.size, vehicles: vehicles.size, holding: 0, stalled: 0, feed_optimistic: 0 };
  const seen = new Set();
  const targets = [];
  for (const [id, tgt] of Object.entries(schedule.targets)) {
    const arrivals = [];
    for (const [key, tu] of trips) {
      const route = tu.trip.route_id; if (!tgt.routes.includes(route)) continue;
      const i = tu.stops.findIndex(s => s.stop_id === tgt.stop_id); if (i < 0) continue;
      const st = tu.stops[i], eta = st.arrival ?? st.departure; if (eta == null || eta < now - C.past_slack_sec || eta > now + 3600) continue;
      const [sched, schedMethod] = matchSched(tgt.sched, tu.trip.trip_id, route, eta);
      const veh = vehicles.get(key);
      const line = schedule.lines[`${route}_${tgt.direction}`];
      // NYCT publishes a vehicle with a status for every train in service; a trip still in the yard has
      // no status (its vehicle timestamp is the scheduled departure) and is not assigned
      const hasPos = !!(veh && veh.stop_id && veh.timestamp && veh.timestamp <= now + 60);
      if (hasPos && !veh.status) veh.status = "IN_TRANSIT_TO";   // the GTFS-Realtime default when current_status is absent
      const started = hasPos || tu.trip.is_assigned === true;
      const pos = hasPos ? describePosition(veh, line, tgt.stop_id, sched, now, C) : null;
      const lateness = sched != null ? eta - sched : null;
      let corroboration = "position_unknown", effective = lateness;
      if (pos && pos.position_lateness_sec != null && lateness != null) { corroboration = pos.position_lateness_sec - lateness > 60 ? "feed_optimistic" : "agree"; effective = Math.max(lateness, pos.position_lateness_sec); }
      if (pos && started && !seen.has(key)) { seen.add(key); if (pos.holding) summary.holding++; if (pos.stalled) summary.stalled++; if (corroboration === "feed_optimistic") summary.feed_optimistic++; }
      arrivals.push({ key, trip_id: tu.trip.trip_id, train_id: tu.trip.train_id, route, feed: tu.feed, eta_ts: eta, sched_ts: sched, sched_method: schedMethod, lateness_sec: lateness, effective_lateness_sec: effective,
        stops_away: i, next_stop_id: tu.stops[0] && tu.stops[0].stop_id, started, position: pos, corroboration,
        track_changed: !!(st.actual_track && st.sched_track && st.actual_track !== st.sched_track), actual_track: st.actual_track });
    }
    arrivals.sort((a, b) => a.eta_ts - b.eta_ts);
    const perRoute = {};
    for (const r of tgt.routes) perRoute[r] = { sched_headway_sec: schedHeadway(tgt.sched, r, now), next_eta_ts: (arrivals.find(a => a.route === r) || {}).eta_ts ?? null };
    // headways and gap/bunching flags per route (trains of one route share a track; other routes may not)
    for (const r of tgt.routes) {
      let prev = null;
      for (const a of arrivals) { if (a.route !== r) continue; if (prev) { a.headway_sec = a.eta_ts - prev.eta_ts; const hw = perRoute[r].sched_headway_sec; if (hw) { a.gap = a.headway_sec > hw * C.gap_ratio; a.bunched = a.headway_sec < hw * C.bunching_ratio; } } prev = a; }
    }
    // "hold persists" scenario: held/stalled trains lose hold_extra_sec more, the feed's optimism is corrected,
    // and followers of the same route cannot arrive within min_headway_sec of their leader
    const disturbed = arrivals.some(a => a.position && (a.position.holding || a.position.stalled));
    if (disturbed) {
      for (const r of tgt.routes) {
        let prev = null;
        for (const a of arrivals) {
          if (a.route !== r) continue;
          let t = a.eta_ts;
          if (a.position && (a.position.holding || a.position.stalled)) t += C.hold_extra_sec;
          if (a.corroboration === "feed_optimistic") t += a.position.position_lateness_sec - a.lateness_sec;
          if (prev != null && t < prev + C.min_headway_sec) t = prev + C.min_headway_sec;
          a.hold_eta_ts = t; prev = t;
        }
      }
    }
    targets.push({ id, stop_id: tgt.stop_id, station_name: tgt.station_name, direction: tgt.direction, routes: tgt.routes, label: tgt.label, per_route: perRoute, arrivals, disturbed,
      n_sched_today: tgt.sched.length });
  }
  return { now, summary, targets };
}

const runBetween = (line, a, b) => { let run = 0; for (let q = a; q < b; q++) { if (line.run_sec[q] == null) return null; run += line.run_sec[q]; } return run; };
const vehKey = trip => `${trip.start_date || ""}|${trip.trip_id}`;

/** Every started train of one line right now: feed projection, reported position, lateness, holds and stalls.
 *  lineSched is the entry of client_lines.json for the same key ([stem, last canonical stop idx, scheduled ts] per trip). */
export function lineBoard(schedule, lineSched, feeds, route, direction, now) {
  const line = schedule.lines[`${route}_${direction}`]; if (!line) return null;
  const C = { hold_sec: 150, stall_slack_sec: 120, ...(schedule.constants || {}) };
  const idx = new Map(line.stops.map((s, i) => [s, i]));
  const vehicles = new Map();
  for (const fd of Object.values(feeds)) for (const v of fd.vehicles) if (v.trip && v.trip.trip_id) vehicles.set(vehKey(v.trip), v);
  const trains = [];
  for (const fd of Object.values(feeds)) for (const tu of fd.trips) {
    if (!tu.trip || tu.trip.route_id !== route || !tu.stops.length) continue;
    if ((tu.stops[0].stop_id || "").slice(-1) !== direction) continue;
    const points = []; for (const s of tu.stops) { const i = idx.get(s.stop_id), t = s.arrival ?? s.departure; if (i != null && t != null) points.push([i, t]); }
    if (!points.length) continue;
    const veh = vehicles.get(vehKey(tu.trip));
    const hasPos = !!(veh && veh.stop_id && veh.timestamp && veh.timestamp <= now + 60);
    if (hasPos && !veh.status) veh.status = "IN_TRANSIT_TO";
    if (!(hasPos || tu.trip.is_assigned === true)) continue;
    const [j, eta] = points[0];
    // schedule at the next stop from the trip's scheduled time at its last canonical stop
    let sched = null, schedMethod = null; const stem = tripStem(tu.trip.trip_id); let best = null, near = null;
    for (const [st, li, ts] of lineSched || []) {
      if (li < j || Math.abs(ts - eta) > 4 * 3600) continue;
      if (st === stem) { if (best == null || Math.abs(ts - eta) < Math.abs(best[1] - eta)) best = [li, ts]; continue; }
      const run = runBetween(line, j, li); if (run == null) continue;
      const at = ts - run;   // this trip's scheduled time at the train's next stop
      if (Math.abs(at - eta) <= NEAREST_TOL_SEC && (near == null || Math.abs(at - eta) < Math.abs(near - eta))) near = at;
    }
    if (best) { const run = runBetween(line, j, best[0]); if (run != null) { sched = best[1] - run; schedMethod = "trip_stem"; } }
    if (sched == null && near != null) { sched = near; schedMethod = "nearest"; }
    const lateness = sched != null ? eta - sched : null;
    let pos = null, corroboration = "position_unknown", effective = lateness;
    if (hasPos) {
      const pj = idx.get(veh.stop_id), since = Math.max(0, now - veh.timestamp);
      let holding = false, stalled = false, expectedRun = null, plate = null;
      if (veh.status === "STOPPED_AT") holding = since >= C.hold_sec && pj !== 0;
      else if (pj != null && pj > 0 && line.run_sec[pj - 1] != null) { expectedRun = line.run_sec[pj - 1]; stalled = since > expectedRun + C.stall_slack_sec; }
      if (sched != null && pj != null && pj <= j) { const run = runBetween(line, pj, j); if (run != null) { const remaining = veh.status === "STOPPED_AT" ? 0 : (expectedRun != null ? Math.max(0, expectedRun - since) : 0); plate = now + remaining - (sched - run); } }
      pos = { status: veh.status, stop_id: veh.stop_id, stop_idx: pj ?? null, stop_name: pj != null ? line.names[pj] : veh.stop_id, since_sec: since, holding, stalled, expected_run_sec: expectedRun, position_lateness_sec: plate };
      if (plate != null && lateness != null) { corroboration = plate - lateness > 60 ? "feed_optimistic" : "agree"; effective = Math.max(lateness, plate); }
    }
    trains.push({ trip_id: tu.trip.trip_id, train_id: tu.trip.train_id, route, points, next_idx: j, next_name: line.names[j], eta_ts: eta, sched_ts: sched, sched_method: schedMethod, lateness_sec: lateness,
      effective_lateness_sec: effective, position: pos, corroboration, track_changed: !!(tu.stops[0].actual_track && tu.stops[0].sched_track && tu.stops[0].actual_track !== tu.stops[0].sched_track) });
  }
  trains.sort((a, b) => b.next_idx - a.next_idx || a.eta_ts - b.eta_ts);
  return { route, direction, now, stops: line.stops.map((s, i) => ({ stop_id: s, name: line.names[i] })), trains,
    n_holding: trains.filter(t => t.position && t.position.holding).length, n_stalled: trains.filter(t => t.position && t.position.stalled).length,
    n_feed_optimistic: trains.filter(t => t.corroboration === "feed_optimistic").length };
}

// ---------------------------------------------------------------- service alerts (Mercury JSON), same rules as sources/alerts.py
const MERCURY_KEY = "transit_realtime.mercury_alert";
const PLANNED_TYPE_PREFIX = ["planned", "weekend service", "buses replace trains", "no midday service", "no weekend service", "special schedule"];
const NOTICE_TYPES = ["boarding change", "station notice", "extra service", "elevator", "escalator", "accessibility", "service reminder", "shuttle bus"];
const tText = (field, lang = "en") => { const trs = (field && field.translation) || []; const t = trs.find(x => x.language === lang) || trs[0]; return t ? (t.text || "") : ""; };
export const alertKind = (type, header) => { const at = (type || "").toLowerCase(); if (PLANNED_TYPE_PREFIX.some(p => at.startsWith(p)) || /planned work|scheduled maintenance/.test((header || "").toLowerCase())) return "planned"; if (NOTICE_TYPES.some(p => at.startsWith(p))) return "notice"; return "delay"; };

/** Alerts active now (or open-ended and updated within 3 h): [{id, type, kind, header, routes, start, end}] */
export function parseAlerts(doc, now) {
  const out = [];
  for (const ent of (doc && doc.entity) || []) {
    const a = ent.alert; if (!a) continue;
    const merc = a[MERCURY_KEY] || {}; const header = tText(a.header_text); const type = merc.alert_type || null;
    const routes = [...new Set((a.informed_entity || []).map(ie => ie.route_id).filter(Boolean))].sort();
    const updated = merc.updated_at != null ? Number(merc.updated_at) : null;
    const periods = (a.active_period && a.active_period.length) ? a.active_period : [{}];
    for (const p of periods) {
      const start = p.start != null ? Number(p.start) : null, end = p.end != null ? Number(p.end) : null;
      const endEff = end != null ? end : (updated != null ? updated + 3 * 3600 : (start != null ? start + 3 * 3600 : null));
      if ((start != null && start > now) || (endEff != null && endEff < now)) continue;
      out.push({ id: ent.id, type, kind: alertKind(type, header), header, routes, start, end, updated });
    }
  }
  // unplanned delays first, then planned, then notices; newest first within a kind
  const rank = { delay: 0, planned: 1, notice: 2 };
  return out.sort((x, y) => rank[x.kind] - rank[y.kind] || (y.start || 0) - (x.start || 0));
}

/** Feeds needed for the configured journeys. */
export const journeyFeeds = schedule => [...new Set((schedule.journeys || []).flatMap(j => j.legs.flatMap(l => l.routes.map(r => (schedule.route_feeds || {})[r]))).filter(Boolean))];

/** Itineraries for every configured journey straight from the feeds: board the next train of the leg's routes at the
 *  origin, ride to the leg's destination using that train's own ETA there, walk the transfer, repeat. */
export function planJourneys(schedule, feeds, now, maxOptions = 4) {
  const C = { hold_sec: 150, stall_slack_sec: 120, ...(schedule.constants || {}) };
  const trips = [], vehicles = new Map();
  for (const fd of Object.values(feeds)) { for (const tu of fd.trips) if (tu.trip && tu.trip.trip_id && tu.stops.length) trips.push(tu); for (const v of fd.vehicles) if (v.trip && v.trip.trip_id) vehicles.set(vehKey(v.trip), v); }
  const state = tu => {   // holding / stalled from the vehicle, if any
    const veh = vehicles.get(vehKey(tu.trip)); if (!(veh && veh.stop_id && veh.timestamp && veh.timestamp <= now + 60)) return null;
    const since = Math.max(0, now - veh.timestamp), status = veh.status || "IN_TRANSIT_TO";
    const line = schedule.lines[`${tu.trip.route_id}_${(tu.stops[0].stop_id || "").slice(-1)}`]; const j = line ? line.stops.indexOf(veh.stop_id) : -1;
    const run = j > 0 && line ? line.run_sec[j - 1] : null;
    return { status, stop_id: veh.stop_id, stop_name: j >= 0 ? line.names[j] : veh.stop_id, since_sec: since, holding: status === "STOPPED_AT" && since >= C.hold_sec && j !== 0, stalled: status !== "STOPPED_AT" && run != null && since > run + C.stall_slack_sec };
  };
  // trains serving (from -> to) for a leg: [board_ts, arrive_ts, trip]
  const rides = (leg, notBefore) => {
    const out = [];
    for (const tu of trips) {
      if (!leg.routes.includes(tu.trip.route_id)) continue;
      const i = tu.stops.findIndex(s => s.stop_id === leg.from_stop); if (i < 0) continue;
      const k = tu.stops.findIndex((s, q) => q > i && s.stop_id === leg.to_stop); if (k < 0) continue;
      const board = tu.stops[i].departure ?? tu.stops[i].arrival, arrive = tu.stops[k].arrival ?? tu.stops[k].departure;
      if (board == null || arrive == null || board < notBefore) continue;
      const veh = vehicles.get(vehKey(tu.trip));
      // a trip still in the yard is listed with its timetable: usable, but shown as not departed
      const started = !!(veh && veh.stop_id && veh.timestamp && veh.timestamp <= now + 60) || tu.trip.is_assigned === true;
      out.push({ board_ts: board, arrive_ts: arrive, tu, started });
    }
    return out.sort((a, b) => a.board_ts - b.board_ts);
  };
  const journeys = [];
  for (const j of schedule.journeys || []) {
    const options = [];
    for (const first of rides(j.legs[0], now).slice(0, maxOptions)) {
      const legs = []; let t = now, ok = true;
      for (let li = 0; li < j.legs.length; li++) {
        const leg = j.legs[li], transfer = li ? (leg.transfer_min || 0) * 60 : 0;
        const ride = li === 0 ? first : rides(leg, t + transfer)[0];
        if (!ride) { ok = false; break; }
        const st = state(ride.tu), desc = ride.tu.trip;
        legs.push({ route: desc.route_id, trip_id: desc.trip_id, train_id: desc.train_id, started: ride.started, from_name: leg.from_name, to_name: leg.to_name, board_ts: ride.board_ts, arrive_ts: ride.arrive_ts,
          wait_sec: ride.board_ts - t - transfer, transfer_sec: transfer, ride_sec: ride.arrive_ts - ride.board_ts, position: st, holding: !!(st && st.holding), stalled: !!(st && st.stalled),
          connection_margin_sec: li ? ride.board_ts - t - transfer : null });
        t = ride.arrive_ts;
      }
      if (!ok) continue;
      const warnings = legs.filter(l => l.holding || l.stalled).map(l => `${l.route} train ${(l.train_id || l.trip_id).trim()} is ${l.holding ? "holding" : "stalled"} at ${l.position.stop_name}`);
      const tight = legs.filter(l => l.connection_margin_sec != null && l.connection_margin_sec < 120).map(l => `tight connection at ${l.from_name}: ${Math.round(l.connection_margin_sec / 60)} min`);
      options.push({ depart_ts: legs[0].board_ts, arrive_ts: t, total_sec: t - now, routes: legs.map(l => l.route), legs, warnings: warnings.concat(tight) });
    }
    options.sort((a, b) => a.arrive_ts - b.arrive_ts);
    journeys.push({ id: j.id, label: j.label, options, best: options[0] || null });
  }
  return { now, journeys };
}

/** Poll the feeds every intervalMs and hand computed boards to onUpdate(board, schedule, feeds).
 *  feedKeys limits which feeds are polled (default: the feeds the monitored platforms need). */
export function createClientLive({ base, onUpdate, onError, intervalMs = 30000, fetchImpl = (u, o) => fetch(u, o), feedKeys = null }) {
  let timer = null, schedule = null, running = false, busy = false, ticks = 0, alerts = null, alertsError = null;
  async function tick() {
    if (busy) return; busy = true;
    try {
      if (!schedule) { const r = await fetchImpl(base + "client_schedule.json", { cache: "no-store" }); if (!r.ok) throw new Error(`client_schedule.json: HTTP ${r.status}`); schedule = await r.json(); }
      const now = schedule.demo_now || Date.now() / 1000, feeds = {}, info = [];
      const wanted = (typeof feedKeys === "function" ? feedKeys(schedule) : feedKeys) || schedule.target_feeds || Object.keys(schedule.feeds);
      const keys = wanted.filter(k => schedule.feeds[k]);
      if (!keys.length) throw new Error("no feed available for this page in data/client_schedule.json");
      await Promise.all(keys.map(async key => {
        const url = schedule.feeds[key], t0 = Date.now();
        const r = await fetchImpl(/^https?:/.test(url) ? url : base + url, { cache: "no-store" }); if (!r.ok) throw new Error(`${key}: HTTP ${r.status}`);
        const fd = parseFeed(new Uint8Array(await r.arrayBuffer())); feeds[key] = fd;
        info.push({ key, feed_ts: fd.timestamp, trips: fd.trips.length, vehicles: fd.vehicles.length, ms: Date.now() - t0 });
      }));
      const board = computeBoard(schedule, feeds, now); board.feeds = info.sort((a, b) => a.key.localeCompare(b.key)); board.schedule_generated_at = schedule.generated_at; board.demo = !!schedule.demo_now;
      if (schedule.alerts_url && !schedule.demo_now && (ticks % 4 === 0 || alerts == null)) {   // the alerts document is large: every 2 minutes
        try { const r = await fetchImpl(schedule.alerts_url, { cache: "no-store" }); if (r.ok) { alerts = parseAlerts(await r.json(), now); alertsError = null; } else alertsError = `HTTP ${r.status}`; }
        catch (e) { alertsError = String(e.message || e); }
      }
      ticks++;
      board.alerts = alerts; board.alerts_error = alertsError;
      await onUpdate(board, schedule, feeds);
    } catch (e) { if (onError) onError(e); }
    finally { busy = false; }
  }
  return {
    start() { if (running) return; running = true; tick(); timer = setInterval(tick, intervalMs); },
    stop() { running = false; if (timer) clearInterval(timer); timer = null; },
    refresh: tick, get running() { return running; },
  };
}
