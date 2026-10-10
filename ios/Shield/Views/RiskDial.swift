import SwiftUI

/// The risk score as a slim three-quarter ring. The ring takes the color of the risk level;
/// the number and the level's name stay in ordinary text.
struct RiskDial: View {
    let risk: Int
    let warnAt: Int
    let endAt: Int

    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    private static let sweep = 0.75
    private static let lineWidth: CGFloat = 12

    var body: some View {
        let level = RiskLevel(risk: risk, warnAt: warnAt, endAt: endAt)
        // The level's name is tucked into the ring's opening. It is laid out under the ring rather
        // than over it, so larger text grows downward, away from the ring. At accessibility text
        // sizes the name is too big for the opening and sits clear of the ring instead.
        VStack(spacing: dynamicTypeSize.isAccessibilitySize ? 2 : -26) {
            ring(color: level.color)
            Text(level.title)
                .font(.headline)
                .lineLimit(1)
                .minimumScaleFactor(0.6)
                .id(level)
                .replaces(reduceMotion: reduceMotion)
        }
        .animation(Motion.change, value: risk)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Scam risk")
        .accessibilityValue("\(risk) out of 100, \(level.title)")
    }

    private func ring(color: Color) -> some View {
        ZStack {
            arc(upTo: 100)
                .stroke(Theme.quietFill, style: StrokeStyle(lineWidth: Self.lineWidth, lineCap: .round))
            arc(upTo: risk)
                .stroke(color, style: StrokeStyle(lineWidth: Self.lineWidth, lineCap: .round))
                .opacity(risk > 0 ? 1 : 0)
            Text(risk, format: .number)
                .font(.system(size: 68, weight: .semibold, design: .rounded))
                .monospacedDigit()
                .contentTransition(reduceMotion ? .opacity : .numericText(value: Double(risk)))
                // Kept inside the ring: on a small screen the dial shrinks and the number shrinks with it.
                .lineLimit(1)
                .minimumScaleFactor(0.4)
                .padding(.horizontal, Self.lineWidth + 8)
        }
        .padding(Self.lineWidth / 2)
        .aspectRatio(1, contentMode: .fit)
    }

    private func arc(upTo value: Int) -> some Shape {
        Circle()
            .trim(from: 0, to: Self.sweep * Double(min(max(value, 0), 100)) / 100)
            .rotation(.degrees(135))
    }
}
