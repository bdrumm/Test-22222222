// Shared by the app and the Live Activity extension.
import SwiftUI

extension Color {
    init(hex: UInt32) {
        self.init(red: Double((hex >> 16) & 0xff) / 255, green: Double((hex >> 8) & 0xff) / 255, blue: Double(hex & 0xff) / 255)
    }
}

enum RouteStyle {
    static let hex: [String: UInt32] = [
        "1": 0xee352e, "2": 0xee352e, "3": 0xee352e, "4": 0x00933c, "5": 0x00933c, "6": 0x00933c, "7": 0xb933ad,
        "A": 0x0039a6, "C": 0x0039a6, "E": 0x0039a6, "B": 0xff6319, "D": 0xff6319, "F": 0xff6319, "M": 0xff6319,
        "G": 0x6cbe45, "J": 0x996633, "Z": 0x996633, "L": 0xa7a9ac, "N": 0xfccc0a, "Q": 0xfccc0a, "R": 0xfccc0a, "W": 0xfccc0a,
        "S": 0x808183, "GS": 0x808183, "FS": 0x808183, "H": 0x808183, "SI": 0x0039a6,
    ]

    static func color(_ route: String) -> Color { Color(hex: hex[route] ?? 0x6b6b6b) }

    static func text(_ route: String) -> Color { ["N", "Q", "R", "W"].contains(route) ? Color(hex: 0x111111) : Color.white }

    static func stateColor(_ state: String, route: String) -> Color {
        switch state {
        case "holding": return Color.orange
        case "stalled": return Color.red
        case "terminal", "unknown": return Color.gray
        default: return color(route)
        }
    }
}

struct RouteBullet: View {
    let route: String
    var size: CGFloat = 22

    var body: some View {
        Text(route)
            .font(.system(size: size * 0.55, weight: .bold))
            .foregroundStyle(RouteStyle.text(route))
            .frame(width: size, height: size)
            .background(Circle().fill(RouteStyle.color(route)))
    }
}

struct RouteBullets: View {
    let routes: [String]
    var size: CGFloat = 22

    var body: some View {
        HStack(spacing: 3) {
            ForEach(routes, id: \.self) { r in RouteBullet(route: r, size: size) }
        }
    }
}
