import Charts
import SwiftUI

/// "What happened" on one call: what Shield noticed and what it did. Never what was said.
struct CallDetailView: View {
    let call: CallSummary

    var body: some View {
        ScrollView {
            CallDetailContent(call: call)
                .padding(.horizontal, 16)
                .padding(.bottom, 24)
        }
        .background(Theme.canvas.ignoresSafeArea())
        .navigationTitle("What happened")
        .inlineNavigationTitle()
    }
}

struct CallDetailContent: View {
    let call: CallSummary

    @Environment(ShieldStore.self) private var store

    var body: some View {
        VStack(spacing: 12) {
            OutcomeHeader(call: call)
            if call.points.count > 1 {
                DetailSection(title: "Risk during the call") {
                    Card {
                        RiskChart(
                            points: call.points,
                            warnAt: store.warnThreshold,
                            endAt: store.endThreshold,
                            tint: OutcomeLook(call.outcome).tint
                        )
                    }
                }
            }
            if !call.flags.isEmpty {
                DetailSection(title: "What Shield noticed") {
                    RowsCard(items: call.flags) { flag in
                        FlagRow(flag: flag)
                    }
                }
            }
            if !call.actions.isEmpty {
                DetailSection(title: "What Shield did") {
                    RowsCard(items: call.actions) { action in
                        ActionRow(action: action)
                    }
                }
            }
            Label {
                Text("Shield keeps what it detected, not what was said. No audio or transcript of this call was stored.")
            } icon: {
                Image(systemName: "lock.fill")
            }
            .font(.footnote)
            .foregroundStyle(.secondary)
            .fixedSize(horizontal: false, vertical: true)
            .padding(.horizontal, 16)
            .padding(.top, 4)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}

/// A title with its content under it.
private struct DetailSection<Content: View>: View {
    let title: String
    @ViewBuilder let content: Content

    var body: some View {
        VStack(spacing: 8) {
            SectionHeader(title: title)
                .padding(.top, 16)
            content
        }
    }
}

/// A card of rows with a hairline between them, as in a system list.
private struct RowsCard<Item: Hashable, Row: View>: View {
    let items: [Item]
    @ViewBuilder let row: (Item) -> Row

    var body: some View {
        Card(padding: 0) {
            VStack(spacing: 0) {
                ForEach(items, id: \.self) { item in
                    VStack(spacing: 0) {
                        row(item)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(.horizontal, 16)
                            .padding(.vertical, 11)
                        if item != items.last {
                            Divider().padding(.leading, 16)
                        }
                    }
                }
            }
        }
    }
}

/// What the call was, who called, when, and for how long.
private struct OutcomeHeader: View {
    let call: CallSummary

    var body: some View {
        let look = OutcomeLook(call.outcome)
        Card {
            VStack(alignment: .leading, spacing: 6) {
                CardLabel(title: look.title, symbol: look.symbol, tint: look.tint)
                Text(callerNumber(call.caller, isDemo: call.isDemo))
                    .font(.title2.weight(.bold))
                    .accessibilityLabel(callerDescription(call.caller, isDemo: call.isDemo))
                VStack(alignment: .leading, spacing: 2) {
                    if let startedAt = call.startedAt {
                        Text(startedAt, format: .dateTime.month().day().year().hour().minute())
                    }
                    if let duration = call.duration {
                        Text("Lasted \(Duration.seconds(duration), format: .units(allowed: [.minutes, .seconds], width: .abbreviated))")
                    }
                }
                .font(.subheadline)
                .foregroundStyle(.secondary)
            }
        }
        .accessibilityElement(children: .combine)
    }
}

/// One thing Shield did, and how soon after the words that set it off.
private struct ActionRow: View {
    let action: CallSummary.Action

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(action.kind == ShieldAction.Kind.end.rawValue ? "Ended the call" : "Warned you")
            if let seconds = action.seconds {
                Text("\(seconds, format: .number.precision(.fractionLength(1))) seconds after the caller’s request")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            }
        }
        .accessibilityElement(children: .combine)
    }
}

/// Risk over the length of the call, against the two thresholds.
struct RiskChart: View {
    let points: [RiskPoint]
    let warnAt: Int
    let endAt: Int
    /// The color of the call's outcome: an ordinary call is not drawn in red.
    let tint: Color

    var body: some View {
        Chart {
            ForEach(points) { point in
                AreaMark(x: .value("Seconds", point.t), y: .value("Risk", point.risk))
                    .interpolationMethod(.monotone)
                    .foregroundStyle(
                        .linearGradient(
                            colors: [tint.opacity(0.22), tint.opacity(0.02)],
                            startPoint: .top,
                            endPoint: .bottom
                        )
                    )
                LineMark(x: .value("Seconds", point.t), y: .value("Risk", point.risk))
                    .interpolationMethod(.monotone)
                    .lineStyle(StrokeStyle(lineWidth: 3, lineCap: .round))
                    .foregroundStyle(tint)
            }
            // The two thresholds are reference lines, so they stay gray: the color is the call's.
            // `Color.secondary`, not `.secondary`, which on a mark means a paler accent color.
            RuleMark(y: .value("Warning", warnAt))
                .lineStyle(StrokeStyle(lineWidth: 1, dash: [4, 4]))
                .foregroundStyle(Color.secondary)
                .annotation(position: .top, alignment: .leading) {
                    Text("Warns").font(.caption2).foregroundStyle(Color.secondary)
                }
            RuleMark(y: .value("End call", endAt))
                .lineStyle(StrokeStyle(lineWidth: 1, dash: [4, 4]))
                .foregroundStyle(Color.secondary)
                .annotation(position: .top, alignment: .leading) {
                    Text("Ends the call").font(.caption2).foregroundStyle(Color.secondary)
                }
        }
        .chartYScale(domain: 0...100)
        .chartYAxis {
            AxisMarks(values: [0, 50, 100])
        }
        .chartXAxisLabel("Seconds into the call")
        .frame(height: 180)
        .accessibilityLabel("Risk during the call")
        .accessibilityValue("Peaked at \(points.map(\.risk).max() ?? 0) out of 100")
    }
}
