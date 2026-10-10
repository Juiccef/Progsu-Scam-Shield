import SwiftUI

struct GatewayInboxView: View {
    @Environment(ShieldStore.self) private var store

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Card {
                VStack(alignment: .leading, spacing: 8) {
                    CardLabel(title: "Protected texting number", symbol: "shield", tint: Theme.safe)
                    if store.gatewayHealth?.enabled == true {
                        Text(store.shieldNumber.isEmpty ? "Loading number…" : store.shieldNumber)
                            .font(.title3.weight(.semibold))
                        Text("SMS addressed to this Shield number is screened before forwarding to the senior. Each forwarded text includes its original sender and a screening footer. Texts sent directly to a personal number bypass Shield.")
                            .font(.subheadline).foregroundStyle(.secondary)
                        if store.gatewayHealth?.forwardingEnabled != true {
                            Text("Forwarding is paused. Approved messages stay queued.")
                                .font(.subheadline).foregroundStyle(Theme.caution)
                        }
                        if store.gatewayHealth?.forwardingEnabled == true && store.gatewayHealth?.smsReady != true {
                            Text("SMS forwarding is unavailable. Approved messages stay queued.")
                                .font(.subheadline).foregroundStyle(Theme.caution)
                        }
                        if store.gatewayHealth?.connected != true {
                            Text("The caregiver iMessage agent has not recently checked in.")
                                .font(.footnote).foregroundStyle(.secondary)
                        }
                    } else {
                        Text("Text protection has not been enabled on this server yet.")
                            .font(.subheadline).foregroundStyle(.secondary)
                    }
                    Text("Forwarded messages share one Shield conversation in Messages. Replies are not forwarded to original senders. Caregiver review alerts arrive through iMessage.")
                        .font(.footnote).foregroundStyle(.secondary)
                    Text("Shield stores protected-number texts encrypted for up to 24 hours for review and delivery. Notifications contain only warning summaries.")
                        .font(.footnote).foregroundStyle(.secondary)
                }
            }
            if let error = store.gatewayError {
                Text(error).font(.subheadline).foregroundStyle(Theme.caution)
            }
            if store.gatewayHealth?.enabled == true {
                SectionHeader(title: "Needs review")
                let held = store.gatewayMessages.filter(\.needsReview)
                if held.isEmpty {
                    Card { Text("No messages awaiting review").foregroundStyle(.secondary) }
                }
                ForEach(held) { message in
                    NavigationLink { GatewayDetailView(messageID: message.id) } label: {
                        GatewayMessageCard(message: message)
                    }
                    .buttonStyle(.plain)
                }
                SectionHeader(title: "Recent activity")
                ForEach(store.gatewayMessages.filter { !$0.needsReview }) { message in
                    NavigationLink { GatewayDetailView(messageID: message.id) } label: {
                        GatewayMessageCard(message: message)
                    }
                    .buttonStyle(.plain)
                }
            }
        }
    }
}

private struct GatewayMessageCard: View {
    let message: GatewayMessage
    var body: some View {
        Card {
            VStack(alignment: .leading, spacing: 8) {
                CardLabel(title: message.statusTitle, symbol: "message",
                          tint: message.needsReview ? Theme.caution : Theme.watchful)
                Text(message.title).font(.headline)
                if let risk = message.risk {
                    Text("Risk score: \(risk)/100").font(.subheadline)
                }
                Text("From \(message.sender)").font(.subheadline)
                Text(message.date, format: .dateTime.month().day().hour().minute())
                    .font(.footnote).foregroundStyle(.secondary)
                if !message.flags.isEmpty {
                    Text(message.flags.map { Flag(id: $0).title }.joined(separator: ", "))
                        .font(.subheadline).foregroundStyle(.secondary)
                }
            }
            .foregroundStyle(.primary)
        }
    }
}

struct GatewayDetailView: View {
    let messageID: String
    @Environment(ShieldStore.self) private var store
    @Environment(\.scenePhase) private var scenePhase
    @State private var message: GatewayMessage?
    @State private var error: String?
    @State private var isWorking = false

    private var latestStatus: String? {
        store.gatewayMessages.first { $0.id == messageID }?.status
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                if let message {
                    GatewayMessageCard(message: message)
                    TimelineView(.periodic(from: .now, by: 30)) { context in
                        let unexpired = message.expiry > context.date
                        if let text = message.text, !text.isEmpty, unexpired {
                            Card {
                                VStack(alignment: .leading, spacing: 12) {
                                    Text("Original message").font(.headline)
                                    Text(verbatim: message.originalSender ?? message.sender)
                                        .font(.subheadline).foregroundStyle(.secondary)
                                    Text(verbatim: text).font(.body)
                                    Text("Sender identity and links have not been verified.")
                                        .font(.footnote).foregroundStyle(.secondary)
                                }
                            }
                        }
                        Card {
                            VStack(alignment: .leading, spacing: 12) {
                                Text(message.explanation).font(.subheadline)
                                if message.needsReview {
                                    Text("Review expires \(message.expiry.formatted(date: .abbreviated, time: .shortened)).")
                                        .font(.footnote).foregroundStyle(.secondary)
                                    if message.reviewEligible && unexpired {
                                        Button("Release to senior") { Task { await decide("release") } }
                                            .buttonStyle(.borderedProminent)
                                            .disabled(isWorking)
                                    }
                                    Button("Discard", role: .destructive) { Task { await decide("discard") } }
                                        .buttonStyle(.bordered)
                                        .disabled(isWorking || !unexpired)
                                    if message.holdReason == "unsupported_content" {
                                        Text("Attachments cannot be reviewed or released in this version.")
                                            .font(.footnote).foregroundStyle(.secondary)
                                    }
                                    if message.holdReason == "message_too_long" || message.holdReason == "transport_changed" {
                                        Text("This message cannot be released in this version.")
                                            .font(.footnote).foregroundStyle(.secondary)
                                    }
                                } else if message.status == "sent" {
                                    Text("Twilio accepted the send. Delivery and whether the senior read it are not yet confirmed.")
                                        .font(.footnote).foregroundStyle(.secondary)
                                } else if message.status == "delivered" {
                                    Text("Twilio reported delivery. This does not confirm the senior read it.")
                                        .font(.footnote).foregroundStyle(.secondary)
                                } else if message.status == "delivery_failed" {
                                    Text("SMS delivery failed. Shield will not automatically resend this text.")
                                        .font(.subheadline).foregroundStyle(Theme.caution)
                                } else if message.status == "delivery_unknown" {
                                    Text("The send could not be confirmed. Check with the senior before sending the text again.")
                                        .font(.subheadline).foregroundStyle(Theme.caution)
                                }
                            }
                        }
                    }
                } else if error == nil {
                    ProgressView("Loading message…")
                }
                if isWorking { ProgressView() }
                if let error {
                    Text(error).foregroundStyle(.secondary)
                    Button("Refresh") { Task { await load() } }
                }
            }
            .padding(16)
        }
        .background(Theme.canvas.ignoresSafeArea())
        .navigationTitle("Text review")
        .environment(\.openURL, OpenURLAction { _ in .discarded })
        .task(id: latestStatus) { await load() }
        .refreshable { await store.refresh(); await load() }
        .onDisappear { message = nil }
        .onChange(of: scenePhase) { _, phase in
            if phase != .active { message = nil }
            else { Task { await load() } }
        }
    }

    @MainActor
    private func load() async {
        do {
            let detail = try await store.gatewayDetail(messageID)
            guard !Task.isCancelled, scenePhase == .active else { return }
            message = detail
            error = nil
        } catch {
            if !Task.isCancelled { self.error = error.localizedDescription }
        }
    }

    @MainActor
    private func decide(_ action: String) async {
        isWorking = true
        defer { isWorking = false }
        do {
            try await store.decideGatewayMessage(messageID, action: action)
            await load()
        } catch {
            await load()
            self.error = error.localizedDescription
        }
    }
}
