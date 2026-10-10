import SwiftUI

/// The caregiver's home screen: is protection on, and what has happened lately.
struct HomeView: View {
    @Environment(ShieldStore.self) private var store
    @AppStorage(SettingsKey.seniorName) private var seniorName = SettingsKey.defaultSeniorName
    @State private var isShowingSettings = false

    var body: some View {
        ScrollView {
            HomeContent(seniorName: seniorName)
                .padding(.horizontal, 16)
                .padding(.bottom, 24)
        }
        .background(Theme.canvas.ignoresSafeArea())
        .navigationTitle("Shield")
        .toolbar {
            if !store.demoScripts.isEmpty {
                ToolbarItem(placement: .primaryAction) {
                    DemoMenu(scripts: store.demoScripts)
                }
            }
            ToolbarItem(placement: .primaryAction) {
                Button("Settings", systemImage: "gearshape") {
                    isShowingSettings = true
                }
            }
        }
        .sheet(isPresented: $isShowingSettings) {
            SettingsView()
        }
        .refreshable {
            await store.refresh()
        }
    }
}

struct HomeContent: View {
    let seniorName: String

    @Environment(ShieldStore.self) private var store

    var body: some View {
        VStack(spacing: 12) {
            ProtectionCard(seniorName: seniorName)
            RecentCalls(calls: store.history)
        }
    }
}

// MARK: - Protection status

private struct ProtectionCard: View {
    let seniorName: String

    @Environment(ShieldStore.self) private var store

    var body: some View {
        if let call = store.call, !call.hasEnded {
            ActiveCallCard(call: call, seniorName: seniorName)
        } else {
            StatusCard(connection: store.connection, seniorName: seniorName, stats: store.stats)
        }
    }
}

/// Whose phone it is, its state in a word, and one line of detail.
private struct StatusCard: View {
    let connection: ShieldStore.Connection
    let seniorName: String
    let stats: CallStats

    var body: some View {
        Card {
            VStack(alignment: .leading, spacing: 6) {
                CardLabel(title: "\(seniorName)’s phone", symbol: symbol, tint: tint)
                Text(title)
                    .font(.title2.weight(.bold))
                Text(detail)
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .accessibilityElement(children: .combine)
    }

    private var symbol: String {
        switch connection {
        case .connected: "checkmark.shield.fill"
        case .connecting: "shield.lefthalf.filled"
        case .notConfigured, .failed: "exclamationmark.shield.fill"
        }
    }

    private var tint: Color {
        switch connection {
        case .connected: Theme.safe
        case .connecting: Theme.watchful
        case .notConfigured, .failed: Theme.caution
        }
    }

    private var title: String {
        switch connection {
        case .connected: "Protected"
        case .connecting: "Connecting"
        case .notConfigured, .failed: "Not connected"
        }
    }

    private var detail: String {
        switch connection {
        case .connected:
            stats.screened == 0 ? "Shield is screening calls to this number." : tally
        case .connecting:
            "Reaching your Shield server."
        case .notConfigured:
            "Add your Shield server’s address in Settings."
        case .failed(let reason):
            "\(reason) Check the address in Settings."
        }
    }

    /// "11 calls screened, 3 scams stopped". A count of zero is left out.
    private var tally: String {
        func counted(_ count: Int, _ one: String, _ many: String) -> String {
            "\(count) \(count == 1 ? one : many)"
        }
        var parts = [counted(stats.screened, "call", "calls") + " screened"]
        if stats.blocked > 0 { parts.append(counted(stats.blocked, "scam", "scams") + " stopped") }
        if stats.warned > 0 { parts.append(counted(stats.warned, "warning", "warnings")) }
        return parts.joined(separator: ", ")
    }
}

private struct ActiveCallCard: View {
    let call: LiveCall
    let seniorName: String

    @Environment(ShieldStore.self) private var store

    var body: some View {
        let level = RiskLevel(risk: call.risk, warnAt: store.warnThreshold, endAt: store.endThreshold)
        Card {
            VStack(alignment: .leading, spacing: 6) {
                CardLabel(title: "\(seniorName)’s phone", symbol: "phone.fill", tint: level.color)
                Text(call.isDemo ? "On a demo call" : "On a call")
                    .font(.title2.weight(.bold))
                Text(level.title)
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                Button {
                    store.showCall()
                } label: {
                    Text("Open the call")
                        .frame(maxWidth: .infinity)
                        .foregroundStyle(Theme.onTint)
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
                .padding(.top, 8)
            }
        }
        .animation(Motion.change, value: level)
    }
}

// MARK: - Recent calls

/// The calls of one day, newest first.
private struct CallDay: Identifiable {
    let id: Date
    let title: String
    var calls: [CallSummary]

    /// Splits calls, already newest first, into days: "Today", "Yesterday", then the date.
    static func days(of calls: [CallSummary], calendar: Calendar = .current) -> [CallDay] {
        var days: [CallDay] = []
        for call in calls {
            let day = call.startedAt.map(calendar.startOfDay(for:)) ?? .distantPast
            if days.last?.id == day {
                days[days.count - 1].calls.append(call)
            } else {
                days.append(CallDay(id: day, title: title(for: day, calendar: calendar), calls: [call]))
            }
        }
        return days
    }

    private static func title(for day: Date, calendar: Calendar) -> String {
        if day == .distantPast { return "Earlier" }
        if calendar.isDateInToday(day) { return "Today" }
        if calendar.isDateInYesterday(day) { return "Yesterday" }
        return day.formatted(.dateTime.weekday(.wide).month(.wide).day())
    }
}

/// Recent calls under the day they came in, the way a phone lists them.
private struct RecentCalls: View {
    let calls: [CallSummary]

    var body: some View {
        if calls.isEmpty {
            VStack(spacing: 8) {
                SectionHeader(title: "Recent calls")
                    .padding(.top, 16)
                Card {
                    Text("No calls yet")
                        .foregroundStyle(.secondary)
                }
            }
        } else {
            ForEach(CallDay.days(of: calls)) { day in
                VStack(spacing: 8) {
                    SectionHeader(title: day.title)
                        .padding(.top, 16)
                    Card(padding: 0) {
                        VStack(spacing: 0) {
                            ForEach(day.calls) { call in
                                VStack(spacing: 0) {
                                    NavigationLink(value: call) {
                                        CallRow(call: call)
                                    }
                                    .buttonStyle(RowButtonStyle())
                                    if call.id != day.calls.last?.id {
                                        Divider().padding(.leading, 16)
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}

/// One call in the list: who called, what came of it, and when.
struct CallRow: View {
    let call: CallSummary

    var body: some View {
        let look = OutcomeLook(call.outcome)
        HStack(spacing: 12) {
            VStack(alignment: .leading, spacing: 2) {
                Text(callerNumber(call.caller, isDemo: call.isDemo))
                    .accessibilityLabel(callerDescription(call.caller, isDemo: call.isDemo))
                OutcomeLine(look: look)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            if let startedAt = call.startedAt {
                Text(startedAt, format: .dateTime.hour().minute())
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            }
            Image(systemName: "chevron.right")
                .font(.footnote.weight(.semibold))
                .foregroundStyle(.tertiary)
                .accessibilityHidden(true)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 11)
        .contentShape(.rect)
    }
}

/// What came of a call, in gray. Only a call Shield acted on gets a symbol, in its color.
private struct OutcomeLine: View {
    let look: OutcomeLook

    var body: some View {
        HStack(spacing: 5) {
            if look.isNotable {
                Image(systemName: look.symbol)
                    .foregroundStyle(look.tint)
                    .accessibilityHidden(true)
            }
            Text(look.title)
                .foregroundStyle(.secondary)
        }
        .font(.subheadline)
    }
}

/// How an outcome is named and colored everywhere in the app.
struct OutcomeLook {
    let title: String
    let symbol: String
    let tint: Color
    /// False for an ordinary call, which needs no one's attention.
    let isNotable: Bool

    init(_ outcome: CallSummary.Outcome) {
        switch outcome {
        case .endedByShield:
            (title, symbol, tint, isNotable) = ("Scam call stopped", "xmark.shield.fill", Theme.danger, true)
        case .warned:
            (title, symbol, tint, isNotable) = ("Suspicious call", "exclamationmark.triangle.fill", Theme.caution, true)
        case .completed:
            (title, symbol, tint, isNotable) = ("Ordinary call", "checkmark.circle.fill", Theme.watchful, false)
        case .inProgress:
            (title, symbol, tint, isNotable) = ("Call in progress", "phone.fill", Theme.safe, true)
        }
    }
}

// MARK: - Demo calls

private struct DemoMenu: View {
    let scripts: [DemoScript]

    @Environment(ShieldStore.self) private var store

    var body: some View {
        Menu("Demo call", systemImage: "play.circle") {
            Section("Scam calls") {
                ForEach(scripts.filter(\.isScam)) { script in
                    Button(script.title) { start(script) }
                }
            }
            Section("Ordinary calls") {
                ForEach(scripts.filter { !$0.isScam }) { script in
                    Button(script.title) { start(script) }
                }
            }
        }
    }

    private func start(_ script: DemoScript) {
        Task { await store.startDemoCall(script) }
    }
}
