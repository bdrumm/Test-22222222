import SwiftUI

@main
struct WhichWayApp: App {
    @State private var data = DataService()
    @State private var presets = PresetStore()
    @State private var location = LocationService()

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environment(data)
                .environment(presets)
                .environment(location)
                .onAppear { data.start() }
        }
    }
}
