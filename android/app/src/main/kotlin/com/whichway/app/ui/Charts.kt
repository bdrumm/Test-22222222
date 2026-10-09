// The drawn views (ios/WhichWay/WhichWay/Views/TrackDiagramView.swift, MareyChartView.swift, RouteMapView.swift,
// the hours strip in PathViews.swift and PredictionChart.swift), on a Compose Canvas.
package com.whichway.app.ui

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.clipRect
import androidx.compose.ui.graphics.drawscope.rotate
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.TextMeasurer
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.drawText
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.rememberTextMeasurer
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.whichway.core.ClientGeometry
import com.whichway.core.ClientSchedule
import com.whichway.core.Fmt
import com.whichway.core.LatLon
import com.whichway.core.LineBoard
import com.whichway.core.LinePrediction
import com.whichway.core.LineTopology
import com.whichway.core.PathOption
import com.whichway.core.PlatformTiming
import com.whichway.core.RouteStyle
import com.whichway.core.Span
import com.whichway.core.shortLabel
import com.whichway.core.trainProgress
import kotlin.math.abs
import kotlin.math.ceil
import kotlin.math.max
import kotlin.math.min

fun stateColor(state: String, route: String): Color = when (state) {
    "holding" -> Color(0xFFFF9800)
    "stalled" -> Color(0xFFD32F2F)
    "terminal", "unknown" -> Color(0xFF9E9E9E)
    else -> routeColor(route)
}

data class DiagramTrain(val id: String, val idx: Double, val state: String, val route: String, val label: String, val sub: String = "", val emphasis: String? = null)
data class DiagramLayer(val id: String, val name: String, val values: List<Double?>, val color: Color, val format: (Double) -> String)

private fun DrawScope.label(tm: TextMeasurer, text: String, at: Offset, size: Float, color: Color, bold: Boolean = false, anchorRight: Boolean = false, centerX: Boolean = false) {
    val style = TextStyle(fontSize = size.sp, color = color, fontWeight = if (bold) FontWeight.SemiBold else FontWeight.Normal)
    val m = tm.measure(text, style)
    val x = when { anchorRight -> at.x - m.size.width; centerX -> at.x - m.size.width / 2f; else -> at.x }
    drawText(m, topLeft = Offset(x, at.y - m.size.height / 2f))
}

/** Horizontal track of one line direction: per-stop layers above, train markers on the track, stop names below. */
@Composable
fun TrackDiagram(line: LineTopology, route: String, fromIdx: Int?, toIdx: Int?, layers: List<DiagramLayer>, trains: List<DiagramTrain>, scrollTo: Int?) {
    val tm = rememberTextMeasurer()
    val density = LocalDensity.current
    val colW = 56f; val left = 44f; val nameW = 96f
    val layerTop = 8f
    val trackY = layerTop + layers.size * 16f + 60f
    val heightPx = trackY + 24f + nameW * 0.87f
    val n = line.stops.size
    val widthPx = left * 2 + colW * max(1, n - 1) + 30f
    val scroll = rememberScrollState()
    LaunchedEffect(scrollTo) { scrollTo?.let { scroll.scrollTo(with(density) { ((left + it * colW - 8f).dp.toPx()).toInt() }.coerceAtLeast(0)) } }
    val onSurface = MaterialTheme.colorScheme.onSurface; val secondary = MaterialTheme.colorScheme.onSurfaceVariant; val bg = MaterialTheme.colorScheme.surface
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        if (layers.isNotEmpty()) Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) { layers.forEach { l -> Caption("● ${l.name}", l.color) } }
        Canvas(Modifier.horizontalScroll(scroll).width(widthPx.dp).height(heightPx.dp)) {
            val s = density.density
            fun x(i: Double) = (left + i * colW).toFloat() * s
            val ty = trackY * s
            layers.forEachIndexed { li, layer ->
                val y = (layerTop + li * 16f + 8f) * s
                layer.values.forEachIndexed { i, v -> if (v != null) label(tm, layer.format(v), Offset(x(i.toDouble()), y), 9f, layer.color, bold = true, centerX = true) }
            }
            drawLine(secondary.copy(alpha = 0.35f), Offset(x(0.0), ty), Offset(x(max(0, n - 1).toDouble()), ty), 4f * s)
            if (fromIdx != null && toIdx != null && toIdx > fromIdx) drawLine(routeColor(route), Offset(x(fromIdx.toDouble()), ty), Offset(x(toIdx.toDouble()), ty), 5f * s)
            line.stops.forEachIndexed { i, sid ->
                val special = i == fromIdx || i == toIdx
                val r = (if (special) 6f else 4f) * s
                drawCircle(if (special) routeColor(route) else bg, r, Offset(x(i.toDouble()), ty))
                drawCircle(if (special) onSurface else secondary, r, Offset(x(i.toDouble()), ty), style = Stroke((if (special) 2f else 1.5f) * s))
                if (i == fromIdx) label(tm, "▲ board", Offset(x(i.toDouble()), ty + 13f * s), 8f, onSurface, bold = true, centerX = true)
                if (i == toIdx) label(tm, "▼ alight", Offset(x(i.toDouble()), ty + 13f * s), 8f, onSurface, bold = true, centerX = true)
                val name = line.name(i).let { if (it.length > 18) it.takeLast(17) + "…" else it }
                rotate(-60f, Offset(x(i.toDouble()), ty + 22f * s)) { label(tm, name, Offset(x(i.toDouble()), ty + 22f * s), 9f, if (special) onSurface else secondary, anchorRight = true) }
            }
            trains.forEachIndexed { ti, t ->
                val cx = x(t.idx); val my = ty - 14f * s
                val c = stateColor(t.state, t.route)
                drawRoundRect(c, Offset(cx - 9f * s, my - 5f * s), Size(18f * s, 10f * s), CornerRadius(3f * s))
                if (t.state == "stopped") drawRoundRect(onSurface, Offset(cx - 9f * s, my - 5f * s), Size(18f * s, 10f * s), CornerRadius(3f * s), style = Stroke(1f * s))
                val ring = when (t.emphasis) { "origin" -> Color(0xFF2E7D32); "connection" -> Color(0xFF8E24AA); else -> null }
                if (ring != null) drawRoundRect(ring, Offset(cx - 12f * s, my - 8f * s), Size(24f * s, 16f * s), CornerRadius(5f * s), style = Stroke(2f * s))
                val ly = my - (18f + (ti % 2) * 18f) * s
                label(tm, t.label, Offset(cx, ly), 8f, onSurface, bold = true, centerX = true)
                if (t.sub.isNotEmpty()) label(tm, t.sub, Offset(cx, ly + 9f * s), 7f, secondary, centerX = true)
            }
        }
    }
}

/** Marey timeline of the selected path: stops down the side, time across, one line per train. */
@Composable
fun MareyChart(option: PathOption, schedule: ClientSchedule, boards: Map<String, LineBoard>, now: Double, backSec: Double = 300.0, horizonSec: Double = 2700.0) {
    val tm = rememberTextMeasurer()
    class LegSpan(val key: String, val keys: List<String>, val start: Int, val end: Int, val from: Int, val to: Int, val route: String)
    val spans = option.legs.mapIndexedNotNull { li, leg -> leg.idx[leg.primaryKey]?.let { ix -> LegSpan(leg.primaryKey, leg.keys, max(0, ix.from - if (li == 0) 3 else 2), ix.to, ix.from, ix.to, leg.primaryRoute) } }
    class Row(val name: String, val leg: Int, val idx: Int)
    val rows = ArrayList<Row>(); val offsets = ArrayList<Int>()
    spans.forEachIndexed { li, sp ->
        val line = schedule.lines[sp.key]
        offsets.add(rows.size)
        if (line == null) return@forEachIndexed
        for (i in sp.start..sp.end) {
            val name = line.name(i)
            if (li > 0 && i == sp.start && rows.isNotEmpty()) {
                val last = rows.last()
                rows[rows.size - 1] = Row(if (last.name == name) name else "${last.name} / $name", last.leg, last.idx)
                offsets[li] = rows.size - 1
                continue
            }
            rows.add(Row(name, li, i))
        }
    }
    val rowH = 16f; val left = 118f; val right = 10f; val top = 14f; val bottom = 22f
    val heightDp = top + max(1, rows.size - 1) * rowH + bottom
    val secondary = MaterialTheme.colorScheme.onSurfaceVariant; val onSurface = MaterialTheme.colorScheme.onSurface
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Text("This path over the next ${(horizonSec / 60).toInt()} minutes", style = MaterialTheme.typography.titleSmall)
        Canvas(Modifier.fillMaxWidth().height(heightDp.dp)) {
            val s = density
            val w = size.width - (left + right) * s
            val t0 = now - backSec; val t1 = now + horizonSec
            fun xp(ts: Double) = (left * s + ((ts - t0) / (t1 - t0)) * w).toFloat()
            fun yp(r: Int) = if (rows.size > 1) (top * s + r.toFloat() / (rows.size - 1) * (size.height - (top + bottom) * s)) else size.height / 2
            fun rowOf(leg: Int, idx: Int): Int? { val sp = spans.getOrNull(leg) ?: return null; if (idx < sp.start || idx > sp.end) return null; return offsets[leg] + (idx - sp.start) }
            rows.forEachIndexed { r, row ->
                val y = yp(r)
                drawLine(secondary.copy(alpha = 0.18f), Offset(left * s, y), Offset(left * s + w, y), 1f * s)
                label(tm, if (row.name.length > 20) row.name.take(19) + "…" else row.name, Offset(left * s - 6f * s, y), 9f, secondary, anchorRight = true)
            }
            var t = ceil(t0 / 600) * 600
            while (t <= t1) {
                drawLine(secondary.copy(alpha = 0.18f), Offset(xp(t), top * s), Offset(xp(t), size.height - bottom * s), 1f * s)
                label(tm, Fmt.hhmm(t), Offset(xp(t), size.height - 8f * s), 9f, secondary, centerX = true)
                t += 600
            }
            drawLine(onSurface.copy(alpha = 0.6f), Offset(xp(now), top * s), Offset(xp(now), size.height - bottom * s), 1.5f * s)
            label(tm, "now", Offset(xp(now) + 3f * s, top * s + 4f * s), 9f, secondary)
            val mine = (option.live?.legs ?: emptyList()).map { it.train.tripId }.toSet()
            val dash = PathEffect.dashPathEffect(floatArrayOf(5f * s, 4f * s)); val dots = PathEffect.dashPathEffect(floatArrayOf(1.5f * s, 3.5f * s))
            spans.forEachIndexed { li, sp ->
                for (k in sp.keys) {
                    val b = boards[k] ?: continue; val bl = schedule.lines[k] ?: continue; val pl = schedule.lines[sp.key] ?: continue
                    val color = routeColor(sp.route)
                    for (tr in b.trains) {
                        fun mapIdx(i: Int): Int? { if (i >= bl.stops.size) return null; val pi = if (k == sp.key) i else pl.stops.indexOf(bl.stops[i]); return if (pi >= 0) rowOf(li, pi) else null }
                        val held = tr.isHeld; val hi = tr.tripId in mine
                        val feedPts = (tr.feedPoints ?: tr.points).mapNotNull { pt -> if (pt.ts < t0 || pt.ts > t1) null else mapIdx(pt.idx)?.let { Offset(xp(pt.ts), yp(it)) } }
                        if (feedPts.size >= 2) drawPath(Path().apply { moveTo(feedPts[0].x, feedPts[0].y); feedPts.drop(1).forEach { lineTo(it.x, it.y) } },
                            if (held) Color(0xFFD32F2F) else if (hi) color else color.copy(alpha = 0.55f), style = Stroke((if (hi) 2.5f else 1.3f) * s, cap = StrokeCap.Round, pathEffect = dash))
                        tr.pred?.let { pr ->
                            val pts = pr.points.mapNotNull { pt -> if (pt.etaTs < t0 || pt.etaTs > t1) null else mapIdx(pt.idx)?.let { Offset(xp(pt.etaTs), yp(it)) } }
                            if (pts.size >= 2) drawPath(Path().apply { moveTo(pts[0].x, pts[0].y); pts.drop(1).forEach { lineTo(it.x, it.y) } },
                                if (pr.holdExtraSec > 0 || pr.knockOnSec >= 60) Color(0xFFD32F2F) else if (hi) color else color.copy(alpha = 0.7f), style = Stroke((if (hi) 2.5f else 1.4f) * s, cap = StrokeCap.Round, pathEffect = dots))
                        }
                        val prog = trainProgress(tr, now - b.now, bl)
                        val j = prog.idx.toInt()
                        mapIdx(j)?.let { r0 ->
                            val r1 = mapIdx(min(bl.stops.size - 1, j + 1)) ?: r0
                            val y = yp(r0) + (yp(r1) - yp(r0)) * (prog.idx - j).toFloat()
                            drawCircle(stateColor(prog.state, sp.route), 4f * s, Offset(xp(now), y))
                        }
                    }
                }
            }
            val it = option.live; val s0 = spans.firstOrNull()
            if (it != null && s0 != null) {
                val r0 = rowOf(0, s0.from); val r1 = rowOf(0, s0.to)
                if (r0 != null && r1 != null) {
                    val pts = arrayListOf(Offset(xp(now), yp(r0)), Offset(xp(it.legs[0].boardTs), yp(r0)), Offset(xp(it.legs[0].arriveTs), yp(r1)))
                    if (it.legs.size > 1 && spans.size > 1) { val r2 = rowOf(1, spans[1].from); val r3 = rowOf(1, spans[1].to); if (r2 != null && r3 != null) { pts.add(Offset(xp(it.legs[1].boardTs), yp(r2))); pts.add(Offset(xp(it.legs[1].arriveTs), yp(r3))) } }
                    drawPath(Path().apply { moveTo(pts[0].x, pts[0].y); pts.drop(1).forEach { lineTo(it.x, it.y) } }, Color(0xFFD32F2F), style = Stroke(2.5f * s, pathEffect = PathEffect.dashPathEffect(floatArrayOf(6f * s, 4f * s))))
                    pts.forEach { drawCircle(Color(0xFFD32F2F), 3.5f * s, it) }
                }
            }
        }
        Caption("Dots: trains now. Dashed: the feed's projection; dotted: the prediction engine (red: held, or held back by the train ahead). The red path is your recommended itinerary.")
    }
}

/** The path drawn from the published geometry: each leg's track, the stops, the trains at their dead-reckoned positions. No map tiles yet. */
@Composable
fun RouteMap(option: PathOption, schedule: ClientSchedule, geo: ClientGeometry, boards: Map<String, LineBoard>, now: Double) {
    val tm = rememberTextMeasurer()
    val bg = MaterialTheme.colorScheme.surface; val onSurface = MaterialTheme.colorScheme.onSurface
    val all = ArrayList<LatLon>()
    class Shape(val route: String, val line: List<LatLon>, val span: List<LatLon>)
    val shapes = option.legs.mapNotNull { leg ->
        val g = geo.lines[leg.primaryKey] ?: return@mapNotNull null; val ix = leg.idx[leg.primaryKey] ?: return@mapNotNull null
        val full = if (g.shape.size >= 2) g.shape.mapNotNull { if (it.size >= 2) LatLon(it[0], it[1]) else null } else (0 until g.coords.size).mapNotNull { g.coord(it) }
        Shape(leg.primaryRoute, full, (ix.from..ix.to).mapNotNull { g.coord(it) })
    }
    class StopPin(val name: String, val c: LatLon, val special: Boolean, val route: String)
    val stops = option.legs.flatMap { leg ->
        val g = geo.lines[leg.primaryKey] ?: return@flatMap emptyList<StopPin>(); val line = schedule.lines[leg.primaryKey] ?: return@flatMap emptyList(); val ix = leg.idx[leg.primaryKey] ?: return@flatMap emptyList()
        (ix.from..ix.to).mapNotNull { i -> g.coord(i)?.let { StopPin(line.name(i), it, i == ix.from || i == ix.to, leg.primaryRoute) } }
    }
    stops.forEach { all.add(it.c) }
    class TrainPin(val c: LatLon, val color: Color, val label: String, val mine: Boolean)
    val mine = (option.live?.legs ?: emptyList()).map { it.train.id }.toSet()
    val trains = option.legs.flatMap { leg -> leg.keys.flatMap { k ->
        val b = boards[k] ?: return@flatMap emptyList<TrainPin>(); val bl = schedule.lines[k] ?: return@flatMap emptyList(); val g = geo.lines[k] ?: return@flatMap emptyList()
        b.trains.mapNotNull { t ->
            val p = trainProgress(t, now - b.now, bl); val j = p.idx.toInt()
            val a = g.coord(j) ?: return@mapNotNull null; val f = p.idx - j; val bb = g.coord(min(bl.stops.size - 1, j + 1)) ?: a
            TrainPin(LatLon(a.lat + (bb.lat - a.lat) * f, a.lon + (bb.lon - a.lon) * f), stateColor(p.state, t.route), shortLabel(t), t.id in mine)
        }
    } }
    if (all.isEmpty()) { Caption("No geometry for this path."); return }
    val minLat = all.minOf { it.lat }; val maxLat = all.maxOf { it.lat }; val minLon = all.minOf { it.lon }; val maxLon = all.maxOf { it.lon }
    val cLat = (minLat + maxLat) / 2; val cLon = (minLon + maxLon) / 2
    val spanLat = max(0.02, (maxLat - minLat) * 1.4); val spanLon = max(0.02, (maxLon - minLon) * 1.4)
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Canvas(Modifier.fillMaxWidth().height(420.dp)) {
            val cosLat = Math.cos(Math.toRadians(cLat))
            val scale = min(size.width / (spanLon * cosLat), size.height / spanLat).toFloat()
            fun pt(c: LatLon) = Offset((size.width / 2 + ((c.lon - cLon) * cosLat * scale)).toFloat(), (size.height / 2 - ((c.lat - cLat) * scale)).toFloat())
            drawRect(bg)
            clipRect(0f, 0f, size.width, size.height) {
            for (sh in shapes) {
                if (sh.line.size >= 2) drawPath(Path().apply { moveTo(pt(sh.line[0]).x, pt(sh.line[0]).y); sh.line.drop(1).forEach { val p = pt(it); lineTo(p.x, p.y) } }, routeColor(sh.route).copy(alpha = 0.45f), style = Stroke(3f * density, cap = StrokeCap.Round))
                if (sh.span.size >= 2) drawPath(Path().apply { moveTo(pt(sh.span[0]).x, pt(sh.span[0]).y); sh.span.drop(1).forEach { val p = pt(it); lineTo(p.x, p.y) } }, routeColor(sh.route), style = Stroke(5f * density, cap = StrokeCap.Round))
            }
            for (st in stops) {
                val p = pt(st.c); val r = (if (st.special) 6f else 3.5f) * density
                drawCircle(if (st.special) routeColor(st.route) else bg, r, p)
                drawCircle(onSurface.copy(alpha = 0.7f), r, p, style = Stroke((if (st.special) 2f else 1f) * density))
                if (st.special) label(tm, st.name, Offset(p.x + 9f * density, p.y), 10f, onSurface, bold = true)
            }
            for (t in trains) {
                val p = pt(t.c)
                drawRoundRect(t.color, Offset(p.x - 8f * density, p.y - 5f * density), Size(16f * density, 10f * density), CornerRadius(3f * density))
                if (t.mine) drawRoundRect(Color(0xFF2E7D32), Offset(p.x - 10f * density, p.y - 7f * density), Size(20f * density, 14f * density), CornerRadius(4f * density), style = Stroke(2f * density))
                label(tm, t.label, Offset(p.x, p.y - 12f * density), 8f, onSurface, centerX = true)
            }
            }
        }
        Caption("Solid: your stretch; faint: the rest of the line. Trains glide between polls along the scheduled running time; the ringed one is yours. Drawn from the published track geometry (no street map yet).")
    }
}

/** One row of 24 cells: seconds lost at each hour, darker is worse, the outlined cell is now. */
@Composable
fun HourStrip(values: List<Double?>, maxV: Double, current: Int, color: Color) {
    val onSurface = MaterialTheme.colorScheme.onSurface; val secondary = MaterialTheme.colorScheme.onSurfaceVariant
    Canvas(Modifier.fillMaxWidth().height(18.dp)) {
        val gap = 2f * density; val cw = (size.width - gap * 23) / 24
        for (h in 0 until 24) {
            val v = values.getOrNull(h)
            val c = if (v != null) color.copy(alpha = (0.15 + 0.85 * min(1.0, v / maxV)).toFloat()) else secondary.copy(alpha = 0.12f)
            val x = h * (cw + gap)
            drawRoundRect(c, Offset(x, 0f), Size(cw, size.height), CornerRadius(2f * density))
            if (h == current) drawRoundRect(onSurface, Offset(x, 0f), Size(cw, size.height), CornerRadius(2f * density), style = Stroke(1.5f * density))
        }
    }
}

/** The engine's projection for one line as a stringline chart (PredictionChart.swift). */
@Composable
fun PredictionChart(line: LineTopology, board: LineBoard, prediction: LinePrediction, focusedTrainId: String?, span: Span?, route: String, now: Double) {
    val tm = rememberTextMeasurer()
    val labelW = 96f; val rowH = 16f; val axisH = 20f
    val n = line.stops.size
    val window: Pair<Int, Int> = run {
        if (n == 0) return@run Pair(0, 0)
        if (span != null) {
            val first = min(span.from, span.to); val last = max(span.from, span.to)
            var lo = max(0, first - 6)
            board.trains.firstOrNull { it.id == focusedTrainId }?.let { lo = min(lo, max(0, it.nextIdx - 1)) }
            val hi = min(n - 1, max(last + 1, lo + 10))
            lo = max(lo, hi - 26 + 1)
            Pair(lo, hi)
        } else { val next = board.trains.map { it.nextIdx }.filter { it >= 0 }; val lo = max(0, (next.minOrNull() ?: 0) - 1); Pair(lo, min(n - 1, lo + 18)) }
    }
    val (lo, hi) = window
    val rows = max(1, hi - lo + 1)
    var horizon = 1800.0
    val focused = board.trains.firstOrNull { it.id == focusedTrainId }
    if (focused != null && span != null) prediction.train(focused.tripId)?.point(max(span.from, span.to))?.let { horizon = min(3600.0, max(horizon, it.hiTs - now + 120)) }
    val onSurface = MaterialTheme.colorScheme.onSurface; val secondary = MaterialTheme.colorScheme.onSurfaceVariant; val bg = MaterialTheme.colorScheme.surface
    val accent = MaterialTheme.colorScheme.primary; val rc = routeColor(route)
    Canvas(Modifier.fillMaxWidth().height((axisH + rows * rowH + 6).dp)) {
        val s = density
        val chart = Rect(labelW * s, axisH * s, size.width, axisH * s + (hi - lo + 1) * rowH * s)
        fun x(ts: Double) = (chart.left + ((ts - now) / horizon) * chart.width).toFloat()
        fun y(idx: Double) = (chart.top + (idx - lo) * rowH * s + rowH * s / 2).toFloat()
        for (i in lo..hi) {
            val yy = y(i.toDouble())
            val isMark = span != null && (i == span.from || i == span.to)
            if (isMark) drawRect(accent.copy(alpha = 0.10f), Offset(0f, yy - rowH * s / 2), Size(size.width, rowH * s))
            drawLine(secondary.copy(alpha = if (isMark) 0.6f else 0.3f), Offset(chart.left, yy), Offset(chart.right, yy), 0.5f * s)
            label(tm, line.name(i).let { if (it.length > 16) it.take(15) + "…" else it }, Offset(0f, yy), 9f, if (isMark) onSurface else secondary, bold = isMark)
        }
        val tickEvery = if (horizon > 2400) 600.0 else 300.0
        var t = ceil(now / tickEvery) * tickEvery
        label(tm, "now", Offset(chart.left + 2f * s, axisH * s / 2), 9f, onSurface, bold = true)
        while (t < now + horizon) {
            val xx = x(t)
            if (xx > chart.left + 44f * s) { drawLine(secondary.copy(alpha = 0.3f), Offset(xx, chart.top), Offset(xx, chart.bottom), 0.5f * s); label(tm, Fmt.hhmm(t), Offset(xx, axisH * s / 2), 9f, secondary, centerX = true) }
            t += tickEvery
        }
        drawLine(secondary, Offset(chart.left, chart.top), Offset(chart.left, chart.bottom), 1f * s)
        val age = now - board.now
        clipRect(chart.left, chart.top, chart.right, chart.bottom) {
            for (tr in board.trains.sortedBy { if (it.id == focusedTrainId) 1 else 0 }) {
                val isF = tr.id == focusedTrainId
                val prog = trainProgress(tr, age, line)
                val pred = prediction.train(tr.tripId) ?: continue
                val pts = pred.points.filter { it.idx >= lo - 1 && it.idx <= hi + 1 }.sortedBy { it.idx }
                if (pts.isEmpty()) continue
                val startVisible = prog.idx >= lo - 0.5 && prog.idx <= hi + 0.5
                if (isF) {
                    val band = Path(); pts.forEachIndexed { i, p -> if (i == 0) band.moveTo(x(p.loTs), y(p.idx.toDouble())) else band.lineTo(x(p.loTs), y(p.idx.toDouble())) }
                    pts.reversed().forEach { band.lineTo(x(it.hiTs), y(it.idx.toDouble())) }; band.close()
                    drawPath(band, rc.copy(alpha = 0.26f))
                    val feed = (tr.feedPoints ?: tr.points).filter { it.idx >= lo - 1 && it.idx <= hi + 1 }.sortedBy { it.idx }
                    if (feed.isNotEmpty()) { val fp = Path().apply { moveTo(chart.left, y(prog.idx)); feed.forEach { lineTo(x(it.ts), y(it.idx.toDouble())) } }; drawPath(fp, secondary, style = Stroke(1.5f * s, pathEffect = PathEffect.dashPathEffect(floatArrayOf(4f * s, 3f * s)))) }
                }
                val path = Path().apply { moveTo(chart.left, y(if (startVisible) prog.idx else pts[0].idx.toDouble())); pts.forEach { lineTo(x(it.etaTs), y(it.idx.toDouble())) } }
                drawPath(path, if (isF) rc else rc.copy(alpha = 0.5f), style = Stroke((if (isF) 3f else 1.5f) * s, cap = StrokeCap.Round))
                pts.filter { it.idx in lo..hi }.forEach { drawCircle(if (isF) rc else rc.copy(alpha = 0.6f), 2f * s, Offset(x(it.etaTs), y(it.idx.toDouble()))) }
                if (startVisible) {
                    val c = Offset(chart.left, y(prog.idx)); val r = (if (isF) 5f else 3.5f) * s
                    if (tr.isHeld) { drawCircle(bg, r, c); drawCircle(Color(0xFFD32F2F), r, c, style = Stroke(2f * s)) } else drawCircle(rc, r, c)
                }
                if (isF && span != null) pts.firstOrNull { it.idx == max(span.from, span.to) }?.let { a -> drawCircle(rc, 5f * s, Offset(x(a.etaTs), y(a.idx.toDouble())), style = Stroke(2f * s)) }
            }
        }
    }
}

/** The predicted arrival of the focused train at the stop it alights: (eta, lo, hi, feed, stop name). */
fun chartArrival(line: LineTopology, board: LineBoard, prediction: LinePrediction, focusedTrainId: String?, span: Span?): List<Any?>? {
    val t = board.trains.firstOrNull { it.id == focusedTrainId } ?: return null
    val s = span ?: return null
    val p = prediction.train(t.tripId) ?: return null
    val idx = max(s.from, s.to)
    val a = p.point(idx) ?: return null
    val feed = (t.feedPoints ?: t.points).firstOrNull { it.idx == idx }?.ts
    fun at(ts: Double) = PlatformTiming.atPlatform(ts, t.route)
    return listOf(at(a.etaTs), at(a.loTs), at(a.hiTs), feed?.let { at(it) }, line.name(idx))
}
