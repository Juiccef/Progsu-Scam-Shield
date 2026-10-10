import SwiftUI

/// The "Texts" page: the texts waiting for the caregiver to decide, the ones that have gone
/// through, and the ones they checked themselves.
struct TextsView: View {
    @Environment(ShieldStore.self) private var store
    @AppStorage(SettingsKey.seniorName) private var seniorName = SettingsKey.defaultSeniorName
    @State private var isCheckingAText = false

    var body: some View {
        let held = store.gatewayMessages.filter(\.needsReview)
        let recent = store.gatewayMessages.filter { !$0.needsReview }
        let pasted = store.messages.filter(\.isPasted)
        // A server with text protection off only scores the texts sent to its number.
        let scored = store.messages.filter { !$0.isPasted }
        let isEmpty = store.gatewayMessages.isEmpty && store.messages.isEmpty
        List {
            if let health = store.gatewayHealth {
                Section {
                    TextProtection(health: health, number: phoneNumber(store.shieldNumber), seniorName: seniorName)
                        // A little more room than a list row gets: this one is the page's headline.
                        .padding(.vertical, 5)
                } footer: {
                    if health.enabled {
                        Text(limits(of: health))
                    }
                }
            }
            if !held.isEmpty {
                Section("Needs Review") {
                    ForEach(held) { text in
                        NavigationLink(value: Page.text(text.id)) {
                            TextRow(text: text)
                        }
                    }
                }
            }
            if !recent.isEmpty {
                Section("Recent") {
                    ForEach(recent) { text in
                        NavigationLink(value: Page.text(text.id)) {
                            TextRow(text: text)
                        }
                    }
                }
            }
            if !scored.isEmpty {
                Section {
                    ForEach(scored) { check in
                        CheckRow(check: check)
                    }
                } header: {
                    Text("Checked Only")
                } footer: {
                    Text("These arrived while text protection was off. Palisade checked them and did not send them on.")
                }
            }
            if !pasted.isEmpty {
                Section("Checked by You") {
                    ForEach(pasted) { check in
                        CheckRow(check: check)
                    }
                }
            }
            if isEmpty {
                Section {
                    emptyState
                }
            }
            if let problem = store.gatewayError {
                Section {
                } footer: {
                    Text("The list of texts could not be brought up to date. \(problem)")
                }
            }
            if store.gatewayHealth?.enabled == true {
                Section {
                } footer: {
                    Label("A text sent to the protected number is kept encrypted for up to 24 hours, so you can review it, then deleted.", systemImage: "lock.fill")
                }
            }
        }
        .groupedListStyle()
        .headerProminence(.increased)
        .animation(Motion.change, value: store.gatewayMessages.map(\.status))
        .navigationTitle("Texts")
        .toolbar {
            if isConnected {
                ToolbarItem(placement: .primaryAction) {
                    Button("Check a Text", systemImage: "text.magnifyingglass") {
                        isCheckingAText = true
                    }
                    .chrome()
                }
            }
        }
        .sheet(isPresented: $isCheckingAText) {
            CheckTextView()
        }
        .refreshable {
            await store.refresh()
        }
        .onAppear {
            store.markGatewayRead()
        }
    }

    private var isConnected: Bool {
        store.connection == .connected
    }

    /// What text protection does not cover, said once, under the card that says it is on.
    private func limits(of health: GatewayHealth) -> String {
        var text = "Texts sent straight to \(seniorName)’s own number are not checked. A reply to a forwarded text does not reach its sender."
        if health.connected != true {
            text += " iMessage alerts are not being sent: the service that sends them has not checked in."
        }
        return text
    }

    @ViewBuilder private var emptyState: some View {
        switch store.connection {
        case .connected:
            ContentUnavailableView {
                Label("No Texts Yet", systemImage: "message")
            } description: {
                if store.gatewayHealth?.enabled == true, !store.shieldNumber.isEmpty {
                    Text("Texts sent to \(phoneNumber(store.shieldNumber)) show up here.")
                } else {
                    Text("Paste a text to see whether it looks like a scam.")
                }
            } actions: {
                Button("Check a Text") {
                    isCheckingAText = true
                }
                .cardButton()
            }
            .listRowBackground(Color.clear)
        case .notConfigured, .failed:
            // Why the page is empty. The way to fix it is on the Summary page.
            ContentUnavailableView(
                "Not Connected",
                systemImage: "exclamationmark.shield",
                description: Text("Texts show up here once Palisade reaches its server.")
            )
            .listRowBackground(Color.clear)
        case .signedOut:
            ContentUnavailableView(
                "Not Signed In",
                systemImage: "exclamationmark.shield",
                description: Text("Texts show up here once you sign in.")
            )
            .listRowBackground(Color.clear)
        case .refused:
            ContentUnavailableView(
                "No Access",
                systemImage: "exclamationmark.shield",
                description: Text("Texts show up here once your Palisade server lets this account in.")
            )
            .listRowBackground(Color.clear)
        case .connecting:
            // Not known to be empty yet: say nothing for the moment it takes.
            EmptyView()
        }
    }
}

// MARK: - Is text protection on

/// Whose texts, the state of their protection in a word, and one line of detail. The same
/// three lines as the card on Summary.
private struct TextProtection: View {
    let health: GatewayHealth
    let number: String
    let seniorName: String

    private enum Standing {
        case protected
        /// The server checks and keeps texts, and has been told not to send them on.
        case paused
        /// The server would send texts on, and has nothing to send them with.
        case cannotSend
        case off
        /// Asked for on the server, and it could not start.
        case broken(String)
    }

    private var standing: Standing {
        if let error = health.error, !health.enabled { return .broken(error) }
        guard health.enabled else { return .off }
        guard health.forwardingEnabled == true else { return .paused }
        return health.smsReady == true ? .protected : .cannotSend
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            CardLabel(title: "\(seniorName)’s Texts", symbol: symbol, tint: tint)
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
        switch standing {
        case .protected: "checkmark.shield.fill"
        case .paused, .cannotSend, .broken: "exclamationmark.shield.fill"
        case .off: "shield.slash.fill"
        }
    }

    private var tint: Color {
        switch standing {
        case .protected: Theme.safe
        case .paused, .cannotSend, .broken: Theme.caution
        case .off: Theme.watchful
        }
    }

    private var title: String {
        switch standing {
        case .protected: "Protected"
        case .paused: "Forwarding Paused"
        case .cannotSend: "Can’t Forward"
        case .off: "Not Set Up"
        case .broken: "Not Working"
        }
    }

    private var detail: String {
        let theNumber = number.isEmpty ? "the protected number" : number
        switch standing {
        case .protected:
            return "Texts sent to \(theNumber) are checked before they reach \(seniorName)."
        case .paused:
            return "Texts sent to \(theNumber) are checked and kept here. Forwarding them to \(seniorName) is switched off on the server."
        case .cannotSend:
            return "Texts sent to \(theNumber) are checked and kept here. The server cannot forward them: it is in practice mode, or has no Twilio account."
        case .off:
            return "This server does not hold texts or forward them. You can still check a text yourself."
        case .broken(let reason):
            return "Text protection could not start on the server. \(reason.hasSuffix(".") ? reason : reason + ".")"
        }
    }
}

// MARK: - Rows

/// One text sent to the protected number: who sent it, where it is now, and when it came.
struct TextRow: View {
    let text: GatewayMessage

    var body: some View {
        ActivityRow(who: senderDescription(text.sender), look: OutcomeLook(text), date: text.date)
    }
}

/// One text that was only checked: pasted by the caregiver, or scored on arrival.
private struct CheckRow: View {
    let check: MessageSummary

    var body: some View {
        ActivityRow(
            who: check.isPasted ? "Pasted Text" : senderDescription(check.sender),
            look: OutcomeLook(check),
            date: check.date
        )
    }
}

/// Who sent a text, in words. The server masks a number to "***1234", and a sender with no
/// number to show (a company's name, say) to "unknown".
func senderDescription(_ masked: String) -> String {
    let digits = masked.filter(\.isNumber)
    return digits.isEmpty ? "Unknown Sender" : "Number ending \(digits)"
}

/// A whole phone number as people write it. The server gives "+14045550100"; a North American
/// number becomes "+1 (404) 555-0100", and anything else is left as it came.
func phoneNumber(_ number: String) -> String {
    let digits = number.dropFirst().filter(\.isNumber)
    guard number.hasPrefix("+1"), digits.count == 11, digits.count == number.count - 1 else { return number }
    let local = digits.dropFirst()
    return "+1 (\(local.prefix(3))) \(local.dropFirst(3).prefix(3))-\(local.suffix(4))"
}

extension OutcomeLook {
    /// Where a text sent to the protected number stands, in the words every screen uses for it.
    init(_ text: GatewayMessage) {
        let warning = "exclamationmark.triangle.fill"
        switch text.state {
        case .held:
            switch text.hold {
            case .notChecked:
                self.init(title: "Could Not Be Checked", symbol: warning, tint: Theme.caution, isNotable: true)
            case .attachment:
                self.init(title: "Has an Attachment", symbol: warning, tint: Theme.caution, isNotable: true)
            case .tooLong:
                self.init(title: "Too Long to Forward", symbol: warning, tint: Theme.caution, isNotable: true)
            case .oldRecord:
                self.init(title: "Old Record", symbol: warning, tint: Theme.caution, isNotable: true)
            case .scamSigns, nil:
                // The raised hand, as for a scam call Palisade stopped: this one was stopped too.
                if text.verdict == .likelyScam {
                    self.init(title: "Likely Scam", symbol: "hand.raised.fill", tint: Theme.danger, isNotable: true)
                } else {
                    self.init(title: "Suspicious Text", symbol: warning, tint: Theme.caution, isNotable: true)
                }
            }
        case .pending:
            self.init(title: "Checking", symbol: "ellipsis.circle.fill", tint: Theme.watchful, isNotable: false)
        case .queued:
            self.init(title: "Waiting to Be Forwarded", symbol: "clock.fill", tint: Theme.watchful, isNotable: false)
        case .sending:
            self.init(title: "Forwarding", symbol: "arrow.up.circle.fill", tint: Theme.watchful, isNotable: false)
        case .sent:
            self.init(title: "Forwarded", symbol: "arrow.up.circle.fill", tint: Theme.watchful, isNotable: false)
        case .delivered:
            self.init(title: "Delivered", symbol: "checkmark.circle.fill", tint: Theme.watchful, isNotable: false)
        case .deliveryFailed:
            self.init(title: "Not Delivered", symbol: warning, tint: Theme.caution, isNotable: true)
        case .deliveryUnknown:
            self.init(title: "Delivery Not Confirmed", symbol: warning, tint: Theme.caution, isNotable: true)
        case .discarded:
            self.init(title: "Discarded", symbol: "trash.fill", tint: Theme.watchful, isNotable: false)
        case .expired:
            self.init(title: "Expired", symbol: "clock.badge.xmark.fill", tint: Theme.watchful, isNotable: false)
        case .unknown:
            self.init(title: "Text", symbol: "message.fill", tint: Theme.watchful, isNotable: false)
        }
    }

    /// What a check made of a text. Nothing was held or sent, so there is no raised hand here.
    init(_ check: MessageSummary) {
        let warning = "exclamationmark.triangle.fill"
        switch check.verdict {
        case .likelyScam:
            self.init(title: "Likely Scam", symbol: warning, tint: Theme.danger, isNotable: true)
        case .suspicious:
            self.init(title: "Suspicious", symbol: warning, tint: Theme.caution, isNotable: true)
        case .ordinary:
            // Not "safe": nothing here says who the sender is or where a link goes.
            self.init(title: "No Strong Scam Signs", symbol: "checkmark.circle.fill", tint: Theme.watchful, isNotable: false)
        }
    }
}
