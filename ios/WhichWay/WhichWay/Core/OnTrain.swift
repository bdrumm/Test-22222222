import Foundation

/// Trains that have already left a station in the direction of travel, on the lines boarding there, with the
/// time they left estimated from their next arrival and the scheduled run: the look-back for a rider who starts
/// a route, or puts it right, from the train they are already on. `boardIdx` and `alightIdx` give, per line
/// key, the stop the rider boarded at and the stop that line's route gets off at; a train past the latter is
/// no use. Most recent departure first.
func trainsAhead(boards: [String: LineBoard], schedule: ClientSchedule, boardIdx: [String: Int], alightIdx: [String: Int],
                 onLegKeys: Set<String>, now: Double, maxAgoSec: Double = 1500) -> [BoardingCandidate] {
    var out: [BoardingCandidate] = []
    for (key, bi) in boardIdx {
        guard let lb = boards[key], let line = schedule.lines[key] else { continue }
        let ai = alightIdx[key]
        for t in lb.trains where t.nextIdx > bi {
            if let ai = ai, t.nextIdx > ai { continue }
            guard let next = t.points.first(where: { $0.idx >= t.nextIdx }), let run = line.runBetween(bi, next.idx) else { continue }
            let board = next.ts - Double(run)
            guard board >= now - maxAgoSec, board <= now + 60 else { continue }
            var stopTs: [Int: Double] = [:]
            for p in t.points where p.idx > bi { stopTs[p.idx] = p.ts }
            out.append(BoardingCandidate(trainId: t.id, key: key, route: t.route, boardTs: board, alightTs: ai.flatMap { stopTs[$0] },
                                         stopsToAlight: ai.map { $0 - bi }, chosen: false, onLeg: onLegKeys.contains(key), boardIdx: bi, alightIdx: ai,
                                         stoppedAtBoardTs: nil, stopTs: stopTs, progressIdx: t.nextIdx))
        }
    }
    return out.sorted { $0.boardTs != $1.boardTs ? $0.boardTs > $1.boardTs : $0.trainId < $1.trainId }
}
