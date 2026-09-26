import SwiftUI

@main
struct WhichWayApp: App {
    @State private var data = DataService()

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environment(data)
                .onAppear { data.start() }
        }
    }
}
