// swift-tools-version:5.9
// The app's non-UI sources (Models and Core, linked from ios/WhichWay/WhichWay) as a package so they can be
// unit-tested on any platform with `swift test`; the fixture under Tests/ comes from the Python reference.
import PackageDescription

let package = Package(
    name: "WhichWayCore",
    platforms: [.iOS(.v17), .macOS(.v14)],
    products: [.library(name: "WhichWayCore", targets: ["WhichWayCore"])],
    targets: [
        .target(name: "WhichWayCore", path: "Sources/WhichWayCore"),
        .testTarget(name: "WhichWayCoreTests", dependencies: ["WhichWayCore"], path: "Tests/WhichWayCoreTests", resources: [.copy("Fixtures")]),
    ]
)
