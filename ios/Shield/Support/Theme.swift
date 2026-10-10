import SwiftUI

/// Shield's look. Screens are drawn in the system's grouped colors with ordinary black and
/// gray text. Green, orange and red each report a state and nothing else: once per card
/// (its label) and on the dial. What can be tapped is the system's blue, the app's accent
/// color, so a button is never mistaken for a state.
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

    /// The corner radius of the system's own grouped sections, so cards match Settings.
    static let cardRadius: CGFloat = 26
}

/// How things move. Nothing in Shield is thrown by a finger, so nothing bounces: every change
/// eases to rest. These three timings are the only ones the app uses.
enum Motion {
    /// A whole screen arriving or leaving.
    static let screen = Animation.smooth(duration: 0.4)
    /// Something on a screen changing: a card arriving, the dial moving, a list growing.
    static let change = Animation.smooth(duration: 0.35)
    /// Content getting out of the way of what replaces it, and a pressed control letting go.
    static let quick = Animation.easeOut(duration: quickSeconds)
    static let quickSeconds = 0.2
    /// How long what is leaving gets to clear before its replacement starts to arrive, so the
    /// two are never drawn over each other.
    static let lead = 0.15
}

/// How a card arrives between things already on screen: it is uncovered from its top edge down
/// while what is below it moves out of the way, so the two never overlap. Kept with Reduce
/// Motion too: what is below has to move either way, and a plain fade would land on top of it.
struct Unfold: Transition {
    func body(content: Content, phase: TransitionPhase) -> some View {
        content
            .mask(alignment: .top) {
                GeometryReader { proxy in
                    RoundedRectangle(cornerRadius: Theme.cardRadius)
                        .frame(height: phase.isIdentity ? proxy.size.height : 0)
                }
            }
            .opacity(phase.isIdentity ? 1 : 0)
    }
}

extension View {
    /// How one word takes the place of another: the old one blurs away as the new one sharpens,
    /// so the two are never legible on top of each other. With Reduce Motion they cross-fade.
    func replaces(reduceMotion: Bool) -> some View {
        transition(reduceMotion ? AnyTransition.opacity : AnyTransition(.blurReplace))
    }
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
        case .calm: "Looks Normal"
        case .watchful: "Listening Closely"
        case .suspicious: "Suspicious"
        case .scam: "Likely Scam"
        }
    }
}

/// A rounded panel on the canvas, for the call screen. Lists draw their own.
struct Card<Content: View>: View {
    @ViewBuilder let content: Content

    var body: some View {
        content
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Theme.surface, in: .rect(cornerRadius: Theme.cardRadius))
    }
}

/// The line that opens a card. Where the card reports a state it is a small symbol and a name
/// in that state's color, the one color the card uses. Where it only names what the card
/// holds, it is the name alone, in gray.
struct CardLabel: View {
    let title: String
    var symbol: String?
    var tint: Color = .secondary

    var body: some View {
        // Laid out by hand: inside a list, a `Label` is given the wide symbol column of a
        // settings row, which pulls the name away from its symbol.
        HStack(spacing: 6) {
            if let symbol {
                Image(systemName: symbol)
                    .accessibilityHidden(true)
            }
            Text(title)
        }
        .font(.subheadline.weight(.semibold))
        .foregroundStyle(tint)
    }
}

extension View {
    /// A button in the call screen's header. Where the system has a glass button (iOS 26) it
    /// is that one, the same as the navigation bar's, so the two screens' buttons match.
    /// Before that it is the quiet button.
    @ViewBuilder
    func headerButton(_ shape: ButtonBorderShape) -> some View {
        if #available(iOS 26, macOS 26, *) {
            buttonStyle(.glass)
                .buttonBorderShape(shape)
                .controlSize(.large)
                .chrome()
        } else {
            buttonStyle(QuietButtonStyle(height: 44))
        }
    }

    /// For the buttons around a screen's edge (its navigation bar, the call screen's header):
    /// drawn in the text color, as the system draws its own. Blue is kept for the one button
    /// a screen is about.
    func chrome() -> some View {
        tint(.primary)
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
            // At least as wide as it is tall: a lone symbol gets a circle.
            .frame(minWidth: height, minHeight: height)
            .background(Theme.surface, in: .capsule)
            .opacity(configuration.isPressed ? 0.55 : 1)
            .animation(configuration.isPressed ? nil : Motion.quick, value: configuration.isPressed)
    }
}
