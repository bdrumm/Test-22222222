// Port of ios/WhichWay/WhichWayShared/Format.swift: time and number formatting, New York local time.
package com.whichway.core

import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale
import kotlin.math.abs
import kotlin.math.roundToInt

object Fmt {
    val NY: ZoneId = ZoneId.of("America/New_York")
    private fun f(p: String) = DateTimeFormatter.ofPattern(p, Locale.US).withZone(NY)
    private val hhmmF = f("h:mm")
    private val hhmmssF = f("h:mm:ss")
    private val dayF = f("yyyy-MM-dd")
    private fun inst(ts: Double) = Instant.ofEpochMilli((ts * 1000).toLong())

    /** 12-hour clock without a suffix: "1:05", "12:40" */
    fun hhmm(ts: Double?): String = ts?.let { hhmmF.format(inst(it)) } ?: "–"
    fun hhmmss(ts: Double?): String = ts?.let { hhmmssF.format(inst(it)) } ?: "–"

    fun minTxt(sec: Double?): String {
        val s = sec ?: return "–"
        val m = s / 60
        return if (m < 10) String.format(Locale.US, "%.1f min", m) else "${m.roundToInt()} min"
    }

    fun mmss(sec: Double): String {
        val s = maxOf(0, sec.toInt())
        return String.format(Locale.US, "%d:%02d", s / 60, s % 60)
    }

    fun signed(sec: Double, unit: String = "s") = "${if (sec >= 0) "+" else "−"}${abs(sec).roundToInt()} $unit"

    fun late(sec: Double?): String {
        val s = sec ?: return "no schedule match"
        if (abs(s) < 60) return "on time"
        val m = (abs(s) / 60).roundToInt()
        return if (s > 0) "$m min late" else "$m min early"
    }

    /** "7:30" for a minute of the day, on the 12-hour clock ("12:15" for 00:15). */
    fun clock(minuteOfDay: Int): String {
        val m = ((minuteOfDay % 1440) + 1440) % 1440
        val h = m / 60 % 12
        return String.format(Locale.US, "%d:%02d", if (h == 0) 12 else h, m % 60)
    }

    /** The New York calendar day of a timestamp, for once-a-day bookkeeping. */
    fun dayStamp(ts: Double): String = dayF.format(inst(ts))

    fun mph(kmh: Double?): String = kmh?.let { String.format(Locale.US, "%.0f mph", it * 0.621371) } ?: "–"

    /** Feet below a tenth of a mile, else miles. */
    fun miles(m: Double): String {
        val mi = m / 1609.344
        if (mi < 0.1) return "${(m * 3.28084 / 10).roundToInt() * 10} ft"
        return String.format(Locale.US, "%.1f mi", mi)
    }

    fun nyHour(ts: Double): Int = inst(ts).atZone(NY).hour
}

/** MTA route colours (ios/WhichWay/WhichWayShared/RouteStyle.swift), as 0xRRGGBB. */
object RouteStyle {
    val hex: Map<String, Int> = mapOf(
        "1" to 0xee352e, "2" to 0xee352e, "3" to 0xee352e, "4" to 0x00933c, "5" to 0x00933c, "6" to 0x00933c, "7" to 0xb933ad,
        "A" to 0x0039a6, "C" to 0x0039a6, "E" to 0x0039a6, "B" to 0xff6319, "D" to 0xff6319, "F" to 0xff6319, "M" to 0xff6319,
        "G" to 0x6cbe45, "J" to 0x996633, "Z" to 0x996633, "L" to 0xa7a9ac, "N" to 0xfccc0a, "Q" to 0xfccc0a, "R" to 0xfccc0a, "W" to 0xfccc0a,
        "S" to 0x808183, "GS" to 0x808183, "FS" to 0x808183, "H" to 0x808183, "SI" to 0x0039a6,
    )
    fun color(route: String): Int = hex[route] ?: 0x6b6b6b
    /** Dark text on the yellow lines, white elsewhere. */
    fun darkText(route: String): Boolean = route in setOf("N", "Q", "R", "W")
}
