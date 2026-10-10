import SwiftUI

/// Shield's look. Screens are drawn in the system's grouped colors with ordinary black and
/// gray text. Color carries meaning and is used once per card (its label) and on the dial.
enum Theme {
    static let canvas = Color.groupedCanvas
    static let surface = Color.groupedSurface
    /// For shapes that need to be seen but not noticed: the dial's empty track, the quieter speech bubble.
    static let quietFill = Color.quietFill

    // The system's own colors, so they follow dark mode and Increase Contrast by themselves.
    static let safe = Color.green
    static let watchful = Color.gray
    static let caution = Color.orange
    static let danger = Color.red

    /// Text on a filled button. The system green is bright in dark mode, where white would wash out.
    static let onTint = Color(light: 0xFFFFFF, dark: 0x0B0B0C)

    /// The corner radius of the system's own grouped sections, so cards match Settings.
    static let cardRadius: CGFloat = 26
}

/// How worried the caregiver should be, given the risk score and the server's thresholds.
enum RiskLevel: Equatable {
    case calm
    case watchful
    case suspicious
    case scam

    init(risk: Int, warnAt: Int, endAt: Int) {
        if risk >= endAt {
            self = .scam
        } else if risk >= warnAt {
            self = .suspicious
        } else if risk >= 30 {
            self = .watchful
        } else {
            self = .calm
        }
    }

    var color: Color {
        switch self {
        case .calm: Theme.safe
        case .watchful: Theme.watchful
        case .suspicious: Theme.caution
        case .scam: Theme.danger
        }
    }

    /// The highlighter color for flagged words in the transcript. Until a call looks suspicious
    /// they are only set in a heavier weight.
    var markColor: Color? {
        switch self {
        case .calm, .watchful: nil
        case .suspicious, .scam: color
        }
    }

    var title: String {
        switch self {
        case .calm: "Looks normal"
        case .watchful: "Listening closely"
        case .suspicious: "Suspicious"
        case .scam: "Likely scam"
        }
    }
}

extension Color {
    /// A color that follows the system's light and dark appearance.
    init(light: UInt32, dark: UInt32) {
        self.init(light: Color(hex: light), dark: Color(hex: dark))
    }

    init(hex: UInt32) {
        self.init(
            red: Double((hex >> 16) & 0xFF) / 255,
            green: Double((hex >> 8) & 0xFF) / 255,
            blue: Double(hex & 0xFF) / 255
        )
    }
}

/// A rounded panel on the canvas.
struct Card<Content: View>: View {
    var padding: CGFloat = 16
    @ViewBuilder let content: Content

    var body: some View {
        content
            .padding(padding)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Theme.surface)
            // Clipped, so a pressed row inside it keeps the card's corners.
            .clipShape(.rect(cornerRadius: Theme.cardRadius))
    }
}

/// The line that opens a card: a small symbol and a name, in the one color that card uses.
struct CardLabel: View {
    let title: String
    let symbol: String
    let tint: Color

    var body: some View {
        Label(title, systemImage: symbol)
            .font(.subheadline.weight(.semibold))
            .foregroundStyle(tint)
    }
}

/// The title above a group of content.
struct SectionHeader: View {
    let title: String

    var body: some View {
        Text(title)
            .font(.title3.weight(.bold))
            .frame(maxWidth: .infinity, alignment: .leading)
            .accessibilityAddTraits(.isHeader)
    }
}

/// A quiet button: the card's color with ordinary text, so that a filled button beside it is
/// the one that stands out.
struct QuietButtonStyle: ButtonStyle {
    var height: CGFloat = 50

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .foregroundStyle(.primary)
            .padding(.horizontal, 14)
            .frame(minHeight: height)
            .background(Theme.surface, in: .capsule)
            .opacity(configuration.isPressed ? 0.55 : 1)
            .animation(configuration.isPressed ? nil : .easeOut(duration: 0.2), value: configuration.isPressed)
    }
}

/// Dims a tapped row the way a list row dims.
struct RowButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .background(configuration.isPressed ? Color.pressedRow : .clear)
            .animation(configuration.isPressed ? nil : .easeOut(duration: 0.25), value: configuration.isPressed)
    }
}
