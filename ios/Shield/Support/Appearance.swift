import SwiftUI

/// How the app is drawn: following the phone, or always light, or always dark. Chosen in Settings.
enum Appearance: String, CaseIterable, Identifiable {
    case automatic
    case light
    case dark

    var id: Self { self }

    var title: String {
        switch self {
        case .automatic: "Automatic"
        case .light: "Light"
        case .dark: "Dark"
        }
    }
}
