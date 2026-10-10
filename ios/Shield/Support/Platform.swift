import SwiftUI

// The few places where iOS and macOS differ. The app ships for iPhone; keeping it buildable
// for the Mac lets the code be type-checked on a machine that has no iOS SDK.

#if canImport(UIKit)
import UIKit

extension Color {
    init(light: Color, dark: Color) {
        self.init(uiColor: UIColor { traits in
            traits.userInterfaceStyle == .dark ? UIColor(dark) : UIColor(light)
        })
    }

    // The system's own grouped-screen colors, the ones Settings and Health are drawn in.
    static let groupedCanvas = Color(uiColor: .systemGroupedBackground)
    static let groupedSurface = Color(uiColor: .secondarySystemGroupedBackground)
    static let pressedRow = Color(uiColor: .systemGray4)
    static let quietFill = Color(uiColor: .systemGray5)
}
#else
import AppKit

extension Color {
    init(light: Color, dark: Color) {
        self.init(nsColor: NSColor(name: nil) { appearance in
            appearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua ? NSColor(dark) : NSColor(light)
        })
    }

    static let groupedCanvas = Color(nsColor: .windowBackgroundColor)
    static let groupedSurface = Color(nsColor: .controlBackgroundColor)
    static let pressedRow = Color(nsColor: .separatorColor)
    static let quietFill = Color(nsColor: .quaternaryLabelColor)
}
#endif

extension View {
    func inlineNavigationTitle() -> some View {
        #if os(iOS)
        navigationBarTitleDisplayMode(.inline)
        #else
        self
        #endif
    }

    /// For fields that hold an address or a token: no autocorrect, no capitals.
    func literalEntry() -> some View {
        #if os(iOS)
        textInputAutocapitalization(.never).autocorrectionDisabled().keyboardType(.URL)
        #else
        autocorrectionDisabled()
        #endif
    }

    func phoneEntry() -> some View {
        #if os(iOS)
        keyboardType(.phonePad)
        #else
        self
        #endif
    }
}

extension Appearance {
    /// Draws every window of the app this way, cross-fading when `animated`.
    ///
    /// Set on the windows themselves rather than with `preferredColorScheme`, which leaves a
    /// sheet that is already open (Settings, where this is chosen) in the old appearance.
    @MainActor
    func apply(animated: Bool) {
        #if canImport(UIKit)
        let style: UIUserInterfaceStyle = switch self {
        case .automatic: .unspecified
        case .light: .light
        case .dark: .dark
        }
        for case let scene as UIWindowScene in UIApplication.shared.connectedScenes {
            for window in scene.windows where window.overrideUserInterfaceStyle != style {
                UIView.transition(with: window, duration: animated ? 0.25 : 0, options: .transitionCrossDissolve) {
                    window.overrideUserInterfaceStyle = style
                }
            }
        }
        #endif
    }
}
