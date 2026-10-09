// Port of the record in ios/WhichWay/WhichWay/Services/Telemetry.swift: one trip, as the opted-in phone records
// it. No location, no raw sensor data, nothing that identifies the rider.
package com.whichway.core

import kotlinx.serialization.Serializable
import kotlin.math.roundToInt

@Serializable
data class TripObservation(
    var id: String,
    var installId: String,
    val appVersion: String,
    var createdTs: Double,
    var routeLabel: String,
    var legs: List<Leg>,
    var transferStation: String? = null,
    var transferWalkSec: Int? = null,
    var predictedBoardTs: Double? = null,
    var predictedArriveTs: Double? = null,
    var expectedSec: Double,
    var schedSec: Int,
    var extraMin: Int,
    var trainLateSec: Double? = null,
    var trainHeld: Boolean,
    var offline: Boolean,
    val startedBy: String,
    var events: List<MotionEvent> = emptyList(),
    var motionSeconds: Int = 0,
    var endedTs: Double? = null,
    var endedBy: String? = null,
    var corroboratedDepartureTs: Double? = null,
    var startDistanceM: Double? = null,
    var arrivedStationTs: Double? = null,
    var platformTs: Double? = null,
    var accessSec: Double? = null,
    var measuredTransferSec: Double? = null,
    var walkSpeedMPerMin: Double? = null,
    var rideAssumed: Boolean? = null,
    var boarded: List<BoardedLeg>? = null,
    var rideStops: List<Int>? = null,
    var withdrawnDepartures: List<Double>? = null,
    var uploaded: Boolean? = null,
    var uploadedGitHub: Boolean? = null,
) {
    @Serializable data class Leg(val line: String, val from: String, val to: String)

    /** The trip is over: what the tracker measured joins the record. */
    fun complete(tl: TripTimeline) {
        events = tl.events.toList(); motionSeconds = tl.motionSeconds
        endedTs = tl.endedTs; endedBy = tl.endedBy
        corroboratedDepartureTs = tl.corroboratedDepartureTs
        startDistanceM = tl.startDistanceM?.let { (it / 50).roundToInt() * 50.0 }
        arrivedStationTs = tl.arrivedStationTs; platformTs = if (tl.platformObserved) tl.platformTs else null
        accessSec = tl.accessSec; measuredTransferSec = tl.transferWalkSec
        walkSpeedMPerMin = tl.walkSpeedMPerMin; rideAssumed = tl.rideAssumed
        boarded = tl.boarded.takeIf { it.isNotEmpty() }?.toList()
        rideStops = tl.rideStops.takeIf { it.isNotEmpty() }?.toList()
        withdrawnDepartures = tl.withdrawnDepartures?.toList()
        if (predictedBoardTs == null) { predictedBoardTs = tl.forecastBoardTs; predictedArriveTs = tl.forecastArriveTs }
    }

    /** Kept when the trip saw anything. */
    val worthKeeping: Boolean get() = events.isNotEmpty() || motionSeconds >= 60 || accessSec != null || walkSpeedMPerMin != null
}
