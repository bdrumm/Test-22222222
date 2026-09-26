import Foundation

enum Fmt {
    static let ny = TimeZone(identifier: "America/New_York") ?? TimeZone.current

    private static func formatter(_ pattern: String) -> DateFormatter {
        let f = DateFormatter()
        f.timeZone = ny
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = pattern
        return f
    }
    private static let hhmmF = formatter("HH:mm")
    private static let hhmmssF = formatter("HH:mm:ss")

    static func hhmm(_ ts: Double?) -> String {
        guard let ts = ts else { return "–" }
        return hhmmF.string(from: Date(timeIntervalSince1970: ts))
    }

    static func hhmmss(_ ts: Double?) -> String {
        guard let ts = ts else { return "–" }
        return hhmmssF.string(from: Date(timeIntervalSince1970: ts))
    }

    static func minTxt(_ sec: Double?) -> String {
        guard let s = sec else { return "–" }
        let m = s / 60
        return m < 10 ? String(format: "%.1f min", m) : "\(Int(m.rounded())) min"
    }

    static func mmss(_ sec: Double) -> String {
        let s = max(0, Int(sec))
        return String(format: "%d:%02d", s / 60, s % 60)
    }

    static func signed(_ sec: Double, unit: String = "s") -> String {
        "\(sec >= 0 ? "+" : "−")\(Int(abs(sec).rounded())) \(unit)"
    }

    static func late(_ sec: Double?) -> String {
        guard let s = sec else { return "no schedule match" }
        if abs(s) < 60 { return "on time" }
        let m = Int((abs(s) / 60).rounded())
        return s > 0 ? "\(m) min late" : "\(m) min early"
    }

    static func kmh(_ v: Double?) -> String {
        guard let v = v else { return "–" }
        return String(format: "%.0f km/h", v)
    }
}
