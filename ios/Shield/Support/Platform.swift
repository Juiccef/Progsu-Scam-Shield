import SwiftUI

// The few places where iOS and macOS differ. The app ships for iPhone; keeping it buildable
// for the Mac lets the code be type-checked on a machine that has no iOS SDK.

#if canImport(UIKit)
import UIKit

extension Color {
    // The system's own grouped-screen colors, the ones Settings and Health are drawn in.
    static let groupedCanvas = Color(uiColor: .systemGroupedBackground)
    static let groupedSurface = Color(uiColor: .secondarySystemGroupedBackground)
    static let quietFill = Color(uiColor: .systemGray5)
}
#else
import AppKit

extension Color {
    static let groupedCanvas = Color(nsColor: .windowBackgroundColor)
    static let groupedSurface = Color(nsColor: .controlBackgroundColor)
    static let quietFill = Color(nsColor: .quaternaryLabelColor)
}
#endif

/// The bar the system floats just above the tab bar, on every page, the way Music keeps its
/// player there. It exists on iPhone from iOS 26.1.
enum TabBarAccessory {
    static var isAvailable: Bool {
        #if os(iOS)
        if #available(iOS 26.1, *) { return true }
        #endif
        return false
    }
}

extension View {
    /// Puts `content` in the tab bar's accessory while `isShown`. Does nothing where there is
    /// no such bar (see `TabBarAccessory.isAvailable`).
    @ViewBuilder
    func tabBarAccessory(isShown: Bool, @ViewBuilder content: () -> some View) -> some View {
        #if os(iOS)
        if #available(iOS 26.1, *) {
            tabViewBottomAccessory(isEnabled: isShown, content: content)
        } else {
            self
        }
        #else
        self
        #endif
    }

    /// The grouped list that Settings and Health are made of. The Mac has no such style.
    func groupedListStyle() -> some View {
        #if os(iOS)
        listStyle(.insetGrouped)
        #else
        self
        #endif
    }

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
                UIView.transition(with: window, duration: animated ? Motion.quickSeconds : 0, options: .transitionCrossDissolve) {
                    window.overrideUserInterfaceStyle = style
                }
            }
        }
        #endif
    }
}
