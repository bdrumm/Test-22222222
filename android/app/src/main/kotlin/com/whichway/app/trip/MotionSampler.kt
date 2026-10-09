// The Android counterpart of ios/WhichWay/WhichWay/Services/MotionSampler.swift: reads gravity and the linear
// (user) acceleration while a route is in progress and hands one summarised second at a time to the recorder.
// The raw samples are reduced here and never stored or sent.
package com.whichway.app.trip

import android.content.Context
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import com.whichway.core.MotionSecond
import kotlin.math.sqrt

private const val G = 9.80665

class MotionSampler(context: Context) {
    private val sm = context.applicationContext.getSystemService(Context.SENSOR_SERVICE) as SensorManager
    private val gravity = sm.getDefaultSensor(Sensor.TYPE_GRAVITY)
    private val linear = sm.getDefaultSensor(Sensor.TYPE_LINEAR_ACCELERATION)
    val isAvailable: Boolean get() = gravity != null && linear != null
    var isRunning = false; private set

    private var g = floatArrayOf(0f, 0f, 1f)
    private var start = 0.0
    private val vert = ArrayList<Double>(); private val hx = ArrayList<Double>(); private val hy = ArrayList<Double>(); private val hz = ArrayList<Double>()
    private var onSecond: ((MotionSecond) -> Unit)? = null


    private val listener = object : SensorEventListener {
        override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) {}
        override fun onSensorChanged(e: SensorEvent) {
            if (e.sensor.type == Sensor.TYPE_GRAVITY) { g = e.values.clone(); return }
            if (e.sensor.type != Sensor.TYPE_LINEAR_ACCELERATION) return
            val now = System.currentTimeMillis() / 1000.0
            val gn = sqrt((g[0] * g[0] + g[1] * g[1] + g[2] * g[2]).toDouble())
            if (gn <= 0) return
            val gx = g[0] / gn; val gy = g[1] / gn; val gz = g[2] / gn
            val ux = e.values[0] / G; val uy = e.values[1] / G; val uz = e.values[2] / G
            val v = ux * gx + uy * gy + uz * gz
            vert.add(v); hx.add(ux - v * gx); hy.add(uy - v * gy); hz.add(uz - v * gz)
            if (start == 0.0) start = now
            if (now - start < 1.0 || vert.size < 5) return
            val n = vert.size.toDouble()
            val mv = vert.sum() / n
            val stepEnergy = vert.sumOf { (it - mv) * (it - mv) } / n
            val mx = hx.sum() / n; val my = hy.sum() / n; val mz = hz.sum() / n
            val push = sqrt(mx * mx + my * my + mz * mz)
            var dev = 0.0
            for (i in hx.indices) { val dx = hx[i] - mx; val dy = hy[i] - my; val dz = hz[i] - mz; dev += dx * dx + dy * dy + dz * dz }
            val second = MotionSecond(start, stepEnergy, push, sqrt(dev / n))
            start = now; vert.clear(); hx.clear(); hy.clear(); hz.clear()
            onSecond?.invoke(second)
        }
    }

    fun start(onSecond: (MotionSecond) -> Unit) {
        if (!isAvailable || isRunning) return
        this.onSecond = onSecond
        start = 0.0; vert.clear(); hx.clear(); hy.clear(); hz.clear()
        sm.registerListener(listener, gravity, 40_000)
        sm.registerListener(listener, linear, 40_000)
        isRunning = true
    }

    fun stop() {
        if (!isRunning) return
        sm.unregisterListener(listener)
        isRunning = false
        onSecond = null
    }
}
