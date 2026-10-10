import SwiftUI

/// How risky the call looks, as a slim three-quarter ring. The ring takes the color of the risk
/// level and holds the level's name, which is what a caregiver reads at a glance. The score
/// itself sits small in the ring's opening.
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
        Group {
            if dynamicTypeSize.isAccessibilitySize {
                // Words this large do not fit inside the ring. The score goes there instead,
                // and the level's name under the ring, where it can take the room it needs.
                VStack(spacing: 2) {
                    ring(color: level.color) {
                        score
                            .font(.system(size: 68, weight: .semibold, design: .rounded))
                            .minimumScaleFactor(0.4)
                    }
                    name(of: level)
                        .font(.headline)
                }
            } else {
                ring(color: level.color) {
                    name(of: level)
                        .font(.title2.weight(.bold))
                        .minimumScaleFactor(0.7)
                }
                .overlay(alignment: .bottom) {
                    score
                        .font(.system(.title3, design: .rounded, weight: .semibold))
                        .foregroundStyle(.secondary)
                }
            }
        }
        .animation(Motion.change, value: risk)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Scam risk")
        .accessibilityValue("\(risk) out of 100, \(level.title)")
    }

    private func name(of level: RiskLevel) -> some View {
        Text(level.title)
            .multilineTextAlignment(.center)
            .lineLimit(2)
            .id(level)
            .replaces(reduceMotion: reduceMotion)
    }

    private var score: some View {
        Text(risk, format: .number)
            .monospacedDigit()
            .contentTransition(reduceMotion ? .opacity : .numericText(value: Double(risk)))
            .lineLimit(1)
    }

    private func ring(color: Color, @ViewBuilder center: () -> some View) -> some View {
        ZStack {
            arc(upTo: 100)
                .stroke(Theme.quietFill, style: StrokeStyle(lineWidth: Self.lineWidth, lineCap: .round))
            arc(upTo: risk)
                .stroke(color, style: StrokeStyle(lineWidth: Self.lineWidth, lineCap: .round))
                .opacity(risk > 0 ? 1 : 0)
            // Kept inside the ring: on a small screen the dial shrinks and its words with it.
            center()
                .padding(.horizontal, Self.lineWidth + 10)
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
