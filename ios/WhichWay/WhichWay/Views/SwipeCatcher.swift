import SwiftUI
import UIKit

/// A horizontal swipe anywhere in the enclosing scroll view, without touching the scroll. A UIKit pan recogniser
/// is attached to the UIScrollView itself: it begins only on clearly sideways motion, recognises alongside the
/// scroll view's own pan (never ahead of it), and stays out of nested horizontal scroll views, maps and controls.
/// SwiftUI drag gestures inside a ScrollView compete with scrolling; this does not.
struct SwipeCatcher: UIViewRepresentable {
    /// +1 for a swipe to the left (next), -1 to the right (previous).
    var onSwipe: (Int) -> Void

    func makeUIView(context: Context) -> Probe {
        let v = Probe()
        v.isUserInteractionEnabled = false
        v.isHidden = true
        v.coordinator = context.coordinator
        return v
    }

    func updateUIView(_ uiView: Probe, context: Context) { context.coordinator.onSwipe = onSwipe }

    func makeCoordinator() -> Coordinator { Coordinator(onSwipe: onSwipe) }

    static func dismantleUIView(_ uiView: Probe, coordinator: Coordinator) { coordinator.detach() }

    final class Probe: UIView {
        weak var coordinator: Coordinator?
        override func didMoveToWindow() {
            super.didMoveToWindow()
            guard window != nil else { return }
            var v: UIView? = superview
            while let s = v, !(s is UIScrollView) { v = s.superview }
            if let sv = v as? UIScrollView {
                coordinator?.attach(to: sv)
            }
        }
    }

    final class Coordinator: NSObject, UIGestureRecognizerDelegate {
        var onSwipe: (Int) -> Void
        private weak var scrollView: UIScrollView?
        private var pan: UIPanGestureRecognizer?

        init(onSwipe: @escaping (Int) -> Void) { self.onSwipe = onSwipe }

        func attach(to sv: UIScrollView) {
            guard scrollView !== sv else { return }
            detach()
            let g = UIPanGestureRecognizer(target: self, action: #selector(panned(_:)))
            g.delegate = self
            g.cancelsTouchesInView = false
            g.delaysTouchesBegan = false
            g.delaysTouchesEnded = false
            g.maximumNumberOfTouches = 1
            sv.addGestureRecognizer(g)
            scrollView = sv
            pan = g
        }

        func detach() {
            if let g = pan, let sv = scrollView { sv.removeGestureRecognizer(g) }
            pan = nil
            scrollView = nil
        }

        @objc private func panned(_ g: UIPanGestureRecognizer) {
            guard g.state == .ended, let v = g.view else { return }
            let t = g.translation(in: v)
            guard abs(t.x) >= 60, abs(t.x) > abs(t.y) * 2 else { return }
            onSwipe(t.x < 0 ? 1 : -1)
        }

        // sideways only, and never over something that pans on its own (a horizontal scroll view, a map, a slider)
        func gestureRecognizerShouldBegin(_ g: UIGestureRecognizer) -> Bool {
            guard let p = g as? UIPanGestureRecognizer, let sv = scrollView else { return false }
            let v = p.velocity(in: sv)
            guard abs(v.x) > abs(v.y) * 2 else { return false }
            var hit = sv.hitTest(p.location(in: sv), with: nil)
            while let h = hit, h !== sv {
                if h is UIControl { return false }
                if h is UIScrollView { return false }
                if h.gestureRecognizers?.contains(where: { $0 is UIPanGestureRecognizer }) == true { return false }
                hit = h.superview
            }
            return true
        }

        func gestureRecognizer(_ g: UIGestureRecognizer, shouldRecognizeSimultaneouslyWith other: UIGestureRecognizer) -> Bool { true }
        func gestureRecognizer(_ g: UIGestureRecognizer, shouldBeRequiredToFailBy other: UIGestureRecognizer) -> Bool { false }
        func gestureRecognizer(_ g: UIGestureRecognizer, shouldRequireFailureOf other: UIGestureRecognizer) -> Bool { false }
    }
}
