import Charts
import SwiftUI

/// "What happened" on one call: what Palisade noticed and what it did. Never what was said.
struct CallDetailView: View {
    let call: CallSummary

    @Environment(ShieldStore.self) private var store

    var body: some View {
        List {
            Section {
                OutcomeHeader(call: call)
                    .padding(.vertical, 5)
            }
            if call.points.count > 1 {
                Section("Risk During the Call") {
                    RiskChart(
                        points: call.points,
                        warnAt: store.warnThreshold,
                        endAt: store.endThreshold,
                        tint: OutcomeLook(call.outcome).tint
                    )
                    .padding(.vertical, 5)
                }
            }
            if !call.flags.isEmpty {
                Section("What Palisade Noticed") {
                    ForEach(call.flags) { flag in
                        FlagRow(flag: flag)
                    }
                }
            }
            if !call.actions.isEmpty {
                Section("What Palisade Did") {
                    ForEach(call.actions, id: \.self) { action in
                        ActionRow(action: action)
                    }
                }
            }
            Section {
            } footer: {
                Label("Palisade keeps what it detected, not what was said. No audio or transcript of this call was stored.", systemImage: "lock.fill")
            }
        }
        .groupedListStyle()
        .headerProminence(.increased)
        .navigationTitle("What Happened")
        .inlineNavigationTitle()
    }
}

/// What the call was, who called, when, and for how long.
private struct OutcomeHeader: View {
    let call: CallSummary

    var body: some View {
        let look = OutcomeLook(call.outcome)
        VStack(alignment: .leading, spacing: 6) {
            CardLabel(title: look.title, symbol: look.symbol, tint: look.tint)
            Text(callerDescription(call.caller, isDemo: call.isDemo))
                .font(.title2.weight(.bold))
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
        .accessibilityElement(children: .combine)
    }
}

/// One thing Palisade did, and how soon after the words that set it off.
private struct ActionRow: View {
    let action: CallSummary.Action

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(action.kind == ShieldAction.Kind.end.rawValue ? "Ended the Call" : "Warned You")
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
                    Text("Ends the Call").font(.caption2).foregroundStyle(Color.secondary)
                }
        }
        .chartYScale(domain: 0...100)
        // The two thresholds are the lines to read. The grid stays out of their way: no
        // vertical lines, and only the bottom and top of the scale across.
        .chartXAxis {
            AxisMarks { _ in
                AxisValueLabel()
            }
        }
        .chartYAxis {
            AxisMarks(values: [0, 100]) { _ in
                AxisGridLine()
                AxisValueLabel()
            }
        }
        .chartXAxisLabel("Seconds into the Call")
        .frame(height: 180)
        .accessibilityLabel("Risk During the Call")
        .accessibilityValue("Peaked at \(points.map(\.risk).max() ?? 0) out of 100")
    }
}
