import SwiftUI

/// The "Alerts" page: everything Palisade stepped into, calls and texts together,
/// newest first. The ordinary ones are on the Calls and Texts pages.
struct AlertsView: View {
    @Environment(ShieldStore.self) private var store

    var body: some View {
        let notices = Notice.all(calls: store.history, texts: store.gatewayMessages, checks: store.messages)
        List {
            Section {
                if !notices.isEmpty {
                    ForEach(notices) { notice in
                        NoticeRow(notice: notice)
                    }
                } else {
                    switch store.connection {
                    case .connected:
                        ContentUnavailableView(
                            "No Alerts",
                            systemImage: "bell",
                            description: Text("When Palisade warns you about a call, ends one, or holds a text, it shows up here.")
                        )
                        .listRowBackground(Color.clear)
                    case .notConfigured, .failed:
                        // Why the page is empty. The way to fix it is on the Summary page.
                        ContentUnavailableView(
                            "Not Connected",
                            systemImage: "exclamationmark.shield",
                            description: Text("Alerts show up here once Palisade reaches its server.")
                        )
                        .listRowBackground(Color.clear)
                    case .signedOut:
                        ContentUnavailableView(
                            "Not Signed In",
                            systemImage: "exclamationmark.shield",
                            description: Text("Alerts show up here once you sign in.")
                        )
                        .listRowBackground(Color.clear)
                    case .refused:
                        ContentUnavailableView(
                            "No Access",
                            systemImage: "exclamationmark.shield",
                            description: Text("Alerts show up here once your Palisade server lets this account in.")
                        )
                        .listRowBackground(Color.clear)
                    case .connecting:
                        // Not known to be empty yet: say nothing for the moment it takes.
                        EmptyView()
                    }
                }
            }
        }
        .groupedListStyle()
        .animation(Motion.change, value: notices.map(\.id))
        .navigationTitle("Alerts")
        .refreshable {
            await store.refresh()
        }
    }
}

private struct NoticeRow: View {
    let notice: Notice

    var body: some View {
        if let page = notice.page {
            NavigationLink(value: page) {
                ActivityRow(who: notice.who, look: notice.look, date: notice.date)
            }
        } else {
            ActivityRow(who: notice.who, look: notice.look, date: notice.date)
        }
    }
}

/// One thing Palisade did that the caregiver should know about: a call it warned about or
/// ended, a text it held, a text it could not get delivered.
struct Notice: Identifiable {
    let id: String
    let who: String
    let look: OutcomeLook
    let date: Date
    /// Where the row leads. Nil for a text that was only scored: there is nothing more to it.
    let page: Page?

    /// Everything worth an alert in what the app already has, newest first.
    static func all(calls: [CallSummary], texts: [GatewayMessage], checks: [MessageSummary]) -> [Notice] {
        var notices: [Notice] = []
        for call in calls where call.outcome == .warned || call.outcome == .endedByShield {
            notices.append(Notice(
                id: "call \(call.id)",
                who: callerDescription(call.caller, isDemo: call.isDemo),
                look: OutcomeLook(call),
                date: call.startedAt ?? .distantPast,
                page: .call(call)
            ))
        }
        for text in texts {
            guard let look = OutcomeLook(noticeOf: text) else { continue }
            notices.append(Notice(
                id: "text \(text.id)", who: senderDescription(text.sender), look: look, date: text.date, page: .text(text.id)
            ))
        }
        // On a server with text protection off, a risky text is scored and nothing else.
        for check in checks where !check.isPasted && check.verdict != .ordinary {
            notices.append(Notice(
                id: "check \(check.id)",
                who: senderDescription(check.sender),
                look: OutcomeLook(noticeOf: check),
                date: check.date,
                page: nil
            ))
        }
        return notices.sorted { $0.date > $1.date }
    }
}

private extension OutcomeLook {
    /// What Palisade did about a text, as an alert says it: what happened then, whatever
    /// was decided since. Nil for a text that went through with nothing to report.
    init?(noticeOf text: GatewayMessage) {
        let warning = "exclamationmark.triangle.fill"
        if text.wasHeld {
            switch (text.hold, text.verdict) {
            case (.scamSigns, .likelyScam):
                self.init(title: "Scam Text Held", symbol: "hand.raised.fill", tint: Theme.danger, isNotable: true)
            case (.scamSigns, _):
                self.init(title: "Suspicious Text Held", symbol: warning, tint: Theme.caution, isNotable: true)
            default:
                self.init(title: "Text Held", symbol: warning, tint: Theme.caution, isNotable: true)
            }
        } else if text.state == .deliveryFailed {
            self.init(title: "Text Not Delivered", symbol: warning, tint: Theme.caution, isNotable: true)
        } else if text.state == .deliveryUnknown {
            self.init(title: "Text Delivery Not Confirmed", symbol: warning, tint: Theme.caution, isNotable: true)
        } else {
            return nil
        }
    }

    /// A risky text that a server without text protection scored as it arrived.
    init(noticeOf check: MessageSummary) {
        let warning = "exclamationmark.triangle.fill"
        if check.verdict == .likelyScam {
            self.init(title: "Scam Text Received", symbol: warning, tint: Theme.danger, isNotable: true)
        } else {
            self.init(title: "Suspicious Text Received", symbol: warning, tint: Theme.caution, isNotable: true)
        }
    }
}
