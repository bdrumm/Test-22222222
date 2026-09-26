import SwiftUI

struct ContentView: View {
    @Environment(DataService.self) private var data
    @Environment(\.scenePhase) private var scenePhase

    var body: some View {
        TabView {
            PlannerView().tabItem { Label("Go", systemImage: "tram.fill") }
            LineBoardView().tabItem { Label("Line", systemImage: "chart.xyaxis.line") }
            SettingsView().tabItem { Label("Settings", systemImage: "gearshape") }
        }
        .onChange(of: scenePhase) { _, phase in
            switch phase {
            case .active: data.start(); data.refreshIfStale()
            case .background: data.stop()
            default: break
            }
        }
    }
}
