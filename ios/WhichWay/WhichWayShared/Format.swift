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
    // 12-hour clock without a suffix: "1:05", "12:40"
    private static let hhmmF = formatter("h:mm")
    private static let hhmmssF = formatter("h:mm:ss")

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

    /// "7:30" for a minute of the day, on the 12-hour clock ("12:15" for 00:15).
    static func clock(_ minuteOfDay: Int) -> String {
        let m = ((minuteOfDay % 1440) + 1440) % 1440
        let h = m / 60 % 12
        return String(format: "%d:%02d", h == 0 ? 12 : h, m % 60)
    }

    private static let dayF = formatter("yyyy-MM-dd")
    /// The New York calendar day of a timestamp, for once-a-day bookkeeping.
    static func dayStamp(_ ts: Double) -> String { dayF.string(from: Date(timeIntervalSince1970: ts)) }

    /// A speed the data keeps in km/h, shown in mph.
    static func mph(_ kmh: Double?) -> String {
        guard let v = kmh else { return "–" }
        return String(format: "%.0f mph", v * 0.621371)
    }

    /// A distance in metres shown the American way: feet below a tenth of a mile, else miles.
    static func miles(_ m: Double) -> String {
        let mi = m / 1609.344
        if mi < 0.1 { return "\(Int((m * 3.28084 / 10).rounded()) * 10) ft" }
        return String(format: "%.1f mi", mi)
    }
}
