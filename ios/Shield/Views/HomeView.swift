import SwiftUI

/// The first page, "Summary": is protection on, and what has happened lately.
struct HomeView: View {
    @Environment(ShieldStore.self) private var store
    @AppStorage(SettingsKey.seniorName) private var seniorName = SettingsKey.defaultSeniorName
    @State private var isShowingSettings = false

    var body: some View {
        List {
            Section {
                ProtectionCard(seniorName: seniorName) {
                    isShowingSettings = true
                }
                // A little more room than a list row gets: this one is the screen's headline.
                .padding(.vertical, 5)
            }
            TextsToReview(texts: store.gatewayMessages.filter(\.needsReview))
            RecentCalls(
                calls: store.history,
                // The list is fetched before the feed connects. Until then it is not known to be empty.
                isLoaded: store.connection == .connected,
                seniorName: seniorName
            )
        }
        .groupedListStyle()
        .headerProminence(.increased)
        // A held text arrives between the card and the calls, which make room for it.
        .animation(Motion.change, value: store.gatewayMessages.filter(\.needsReview).map(\.id))
        .navigationTitle("Summary")
        .toolbar {
            if !store.demoScripts.isEmpty {
                ToolbarItem(placement: .primaryAction) {
                    DemoMenu(scripts: store.demoScripts) {
                        Label("Demo Call", systemImage: "play.circle")
                    }
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
    let openSettings: () -> Void

    @Environment(ShieldStore.self) private var store

    var body: some View {
        if let call = store.call, !call.hasEnded {
            ActiveCallCard(call: call, seniorName: seniorName)
        } else {
            StatusCard(
                connection: store.connection,
                seniorName: seniorName,
                stats: store.stats,
                openSettings: openSettings
            )
        }
    }
}

/// Whose phone it is, its state in a word, and one line of detail. When Palisade is not
/// connected, the way to fix that is offered here, where the problem is stated.
private struct StatusCard: View {
    let connection: ShieldStore.Connection
    let seniorName: String
    let stats: CallStats
    let openSettings: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
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
            switch connection {
            case .signedOut:
                SignInButton(isOnCard: true)
            case .notConfigured, .refused, .failed:
                // Settings is also where someone the server turned down can sign out.
                Button("Open Settings", action: openSettings)
                    .cardButton()
            case .connecting, .connected:
                EmptyView()
            }
        }
    }

    private var symbol: String {
        switch connection {
        case .connected: "checkmark.shield.fill"
        case .connecting: "shield.lefthalf.filled"
        case .notConfigured, .signedOut, .refused, .failed: "exclamationmark.shield.fill"
        }
    }

    private var tint: Color {
        switch connection {
        case .connected: Theme.safe
        case .connecting: Theme.watchful
        case .notConfigured, .signedOut, .refused, .failed: Theme.caution
        }
    }

    private var title: String {
        switch connection {
        case .connected: "Protected"
        case .connecting: "Connecting"
        case .signedOut: "Not Signed In"
        case .refused: "No Access"
        case .notConfigured, .failed: "Not Connected"
        }
    }

    private var detail: String {
        switch connection {
        case .connected:
            stats.screened == 0 ? "Palisade is screening calls to this number." : tally
        case .connecting:
            "Reaching your Palisade server."
        case .notConfigured:
            "Add your Palisade server’s address in Settings."
        case .signedOut:
            "Sign in to see calls to this phone."
        case .refused(let reason):
            reason
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
            Text(call.statusTitle(warnAt: store.warnThreshold, endAt: store.endThreshold))
                .font(.subheadline)
                .foregroundStyle(.secondary)
            // Where the system has a bar above the tab bar, that bar is the way back to the
            // call, on every page. A second button here would say the same thing twice.
            if !TabBarAccessory.isAvailable {
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
        }
        .animation(Motion.change, value: level)
    }
}

// MARK: - Texts to review

/// The texts Palisade is holding for the caregiver's decision. Nothing while there are none.
private struct TextsToReview: View {
    let texts: [GatewayMessage]

    @Environment(ShieldStore.self) private var store

    private static let shown = 3

    var body: some View {
        if !texts.isEmpty {
            Section {
                ForEach(texts.prefix(Self.shown)) { text in
                    NavigationLink(value: Page.text(text.id)) {
                        TextRow(text: text)
                    }
                }
            } header: {
                HStack(alignment: .firstTextBaseline) {
                    Text("Texts to Review")
                    Spacer()
                    if texts.count > Self.shown {
                        Button("Show All") {
                            store.tab = .texts
                        }
                        // An ordinary link beside the heading, not a second heading.
                        .font(.body)
                    }
                }
            }
        }
    }
}

// MARK: - Recent calls

/// The newest calls, the way a phone lists them. The rest are on the Calls page.
private struct RecentCalls: View {
    let calls: [CallSummary]
    let isLoaded: Bool
    let seniorName: String

    @Environment(ShieldStore.self) private var store

    private static let shown = 3

    var body: some View {
        if !calls.isEmpty {
            Section {
                ForEach(calls.prefix(Self.shown)) { call in
                    NavigationLink(value: Page.call(call)) {
                        CallRow(call: call)
                    }
                }
            } header: {
                HStack(alignment: .firstTextBaseline) {
                    Text("Recent Calls")
                    Spacer()
                    if calls.count > Self.shown {
                        Button("Show All") {
                            store.tab = .calls
                        }
                        // An ordinary link beside the heading, not a second heading.
                        .font(.body)
                    }
                }
            }
        } else if isLoaded {
            Section {
                NoCallsYet(seniorName: seniorName)
            }
        }
    }
}

/// An empty list of calls, as the system's own empty state: what will turn up here, and a
/// demo call to try if the server offers them.
struct NoCallsYet: View {
    let seniorName: String

    @Environment(ShieldStore.self) private var store

    var body: some View {
        ContentUnavailableView {
            Label("No Calls Yet", systemImage: "phone")
        } description: {
            Text("Calls to \(seniorName)’s phone show up here.")
        } actions: {
            if !store.demoScripts.isEmpty {
                DemoMenu(scripts: store.demoScripts) {
                    Text("Start a Demo Call")
                }
                .cardButton()
            }
        }
        // On the canvas, as the system's empty states are, not in a list's box.
        .listRowBackground(Color.clear)
    }
}

/// One call in the list: who called, what came of it, and when.
struct CallRow: View {
    let call: CallSummary

    var body: some View {
        ActivityRow(
            who: callerDescription(call.caller, isDemo: call.isDemo),
            look: OutcomeLook(call),
            date: call.startedAt
        )
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

/// A call or a text in a list, each drawn the same way: who it came from, what came of it,
/// and when.
struct ActivityRow: View {
    let who: String
    let look: OutcomeLook
    let date: Date?

    var body: some View {
        // The day sits at the end of the row while everything fits on one line. When it does
        // not (large text), it goes underneath, so the caller is not squeezed into a column.
        ViewThatFits(in: .horizontal) {
            HStack(spacing: 12) {
                whoAndWhat
                    .frame(maxWidth: .infinity, alignment: .leading)
                when
            }
            VStack(alignment: .leading, spacing: 2) {
                whoAndWhat
                when
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private var whoAndWhat: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(who)
            OutcomeLine(look: look)
        }
    }

    @ViewBuilder private var when: some View {
        if let date {
            Text(CallRow.when(date))
                .font(.subheadline)
                .foregroundStyle(.secondary)
        }
    }
}

/// What came of a call or a text, in gray. Only one Palisade acted on gets a symbol, in its color.
struct OutcomeLine: View {
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

/// How an outcome is named and colored everywhere in the app: a call's here, a text's in
/// `TextsView.swift`.
struct OutcomeLook {
    let title: String
    let symbol: String
    let tint: Color
    /// False for an ordinary call or text, which needs no one's attention.
    let isNotable: Bool
}

extension OutcomeLook {
    init(_ outcome: CallSummary.Outcome) {
        switch outcome {
        case .endedByShield:
            // The raised hand is the system's own sign for something blocked. An X on a
            // shield could be read as protection that failed.
            self.init(title: "Scam Call Stopped", symbol: "hand.raised.fill", tint: Theme.danger, isNotable: true)
        case .warned:
            self.init(title: "Suspicious Call", symbol: "exclamationmark.triangle.fill", tint: Theme.caution, isNotable: true)
        case .completed:
            self.init(title: "Ordinary Call", symbol: "checkmark.circle.fill", tint: Theme.watchful, isNotable: false)
        case .inProgress:
            self.init(title: "Call in Progress", symbol: "phone.fill", tint: Theme.safe, isNotable: true)
        }
    }

    /// A call as its row and its page name it. Once the caregiver has said Palisade got it
    /// wrong, it is no longer called what Palisade took it for. Gray, and with no sign in a
    /// row, like an ordinary call: the mark is the caregiver's word, not something to act on.
    /// The sign is a turned-back arrow and not a flag, because "Flagged" on the Calls page
    /// already means the calls Palisade stepped into.
    init(_ call: CallSummary) {
        if call.markedWrong == true {
            self.init(
                title: call.wasSteppedInto ? "Marked as Not a Scam" : "Marked as a Scam",
                symbol: "arrow.uturn.backward.circle.fill",
                tint: Theme.watchful,
                isNotable: false
            )
        } else {
            self.init(call.outcome)
        }
    }
}

// MARK: - Demo calls

/// The demo calls the server offers, behind whatever label the place calls for: a symbol in
/// the toolbar, words in the empty list.
struct DemoMenu<MenuLabel: View>: View {
    let scripts: [DemoScript]
    @ViewBuilder let label: MenuLabel

    @Environment(ShieldStore.self) private var store

    var body: some View {
        Menu {
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
        } label: {
            label
        }
    }

    private func start(_ script: DemoScript) {
        Task { await store.startDemoCall(script) }
    }
}
