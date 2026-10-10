import SwiftUI

/// The caregiver's home screen: is protection on, and what has happened lately.
struct HomeView: View {
    @Environment(ShieldStore.self) private var store
    @AppStorage(SettingsKey.seniorName) private var seniorName = SettingsKey.defaultSeniorName
    @State private var isShowingSettings = false

    var body: some View {
        List {
            Section {
                ProtectionCard(seniorName: seniorName)
                    // A little more room than a list row gets: this one is the screen's headline.
                    .padding(.vertical, 5)
            }
            RecentCalls(calls: store.history)
        }
        .groupedListStyle()
        .headerProminence(.increased)
        .navigationTitle("Shield")
        .toolbar {
            if !store.demoScripts.isEmpty {
                ToolbarItem(placement: .primaryAction) {
                    DemoMenu(scripts: store.demoScripts)
                        .chrome()
                }
            }
            ToolbarItem(placement: .primaryAction) {
                Button("Settings", systemImage: "gearshape") {
                    isShowingSettings = true
                }
                .chrome()
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
        VStack(alignment: .leading, spacing: 6) {
            CardLabel(title: "\(seniorName)’s Phone", symbol: symbol, tint: tint)
            Text(title)
                .font(.title2.weight(.bold))
            Text(detail)
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
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
        case .notConfigured, .failed: "Not Connected"
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
        VStack(alignment: .leading, spacing: 6) {
            // Until someone has spoken there is no level to report, here or on the call screen.
            CardLabel(
                title: "\(seniorName)’s Phone",
                symbol: "phone.fill",
                tint: call.hasSpeech ? level.color : Theme.watchful
            )
            Text(call.isDemo ? "On a Demo Call" : "On a Call")
                .font(.title2.weight(.bold))
            Text(call.hasSpeech ? level.title : call.phase == .ringing ? "Ringing" : "Listening")
                .font(.subheadline)
                .foregroundStyle(.secondary)
            Button {
                store.showCall()
            } label: {
                Text("Open the Call")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.large)
            .padding(.top, 8)
        }
        .animation(Motion.change, value: level)
    }
}

// MARK: - Recent calls

/// Recent calls in one list, newest first, the way a phone lists them.
private struct RecentCalls: View {
    let calls: [CallSummary]

    var body: some View {
        Section("Recent Calls") {
            if calls.isEmpty {
                Text("No Calls Yet")
                    .foregroundStyle(.secondary)
            } else {
                ForEach(calls) { call in
                    NavigationLink(value: call) {
                        CallRow(call: call)
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
        HStack(spacing: 12) {
            VStack(alignment: .leading, spacing: 2) {
                Text(callerDescription(call.caller, isDemo: call.isDemo))
                OutcomeLine(look: OutcomeLook(call.outcome))
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            if let startedAt = call.startedAt {
                Text(Self.when(startedAt))
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            }
        }
    }

    /// When a call came in, the way a phone's recent calls say it: the time if it was today,
    /// then "Yesterday", then the day of the week, then the date. The summary has the rest.
    static func when(_ date: Date, now: Date = .now, calendar: Calendar = .current) -> String {
        let daysAgo = calendar.dateComponents(
            [.day], from: calendar.startOfDay(for: date), to: calendar.startOfDay(for: now)
        ).day
        switch daysAgo {
        case 0: return date.formatted(.dateTime.hour().minute())
        case 1: return "Yesterday"
        case .some(2..<7): return date.formatted(.dateTime.weekday(.wide))
        default: return date.formatted(.dateTime.month(.defaultDigits).day().year(.twoDigits))
        }
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
            // The raised hand is the system's own sign for something blocked. An X on a
            // shield could be read as protection that failed.
            (title, symbol, tint, isNotable) = ("Scam Call Stopped", "hand.raised.fill", Theme.danger, true)
        case .warned:
            (title, symbol, tint, isNotable) = ("Suspicious Call", "exclamationmark.triangle.fill", Theme.caution, true)
        case .completed:
            (title, symbol, tint, isNotable) = ("Ordinary Call", "checkmark.circle.fill", Theme.watchful, false)
        case .inProgress:
            (title, symbol, tint, isNotable) = ("Call in Progress", "phone.fill", Theme.safe, true)
        }
    }
}

// MARK: - Demo calls

private struct DemoMenu: View {
    let scripts: [DemoScript]

    @Environment(ShieldStore.self) private var store

    var body: some View {
        Menu("Demo Call", systemImage: "play.circle") {
            Section("Scam Calls") {
                ForEach(scripts.filter(\.isScam)) { script in
                    Button(script.title) { start(script) }
                }
            }
            Section("Ordinary Calls") {
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
