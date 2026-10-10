import SwiftUI

/// Checks pasted text and shows summaries for texts sent to the Shield number.
struct MessagesView: View {
    @Environment(ShieldStore.self) private var store
    @State private var text = ""
    @State private var isChecking = false
    @State private var result: MessageSummary?
    @State private var error: String?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                GatewayInboxView()
                Card {
                    VStack(alignment: .leading, spacing: 12) {
                        Text("Check a text")
                            .font(.headline)
                        Text("You can also paste a text for a separate check. This does not deliver it to the senior.")
                            .font(.subheadline)
                            .foregroundStyle(.secondary)
                        TextEditor(text: $text)
                            .frame(minHeight: 130)
                            .accessibilityLabel("Message to check")
                        Text("Message text is sent to your Shield server and its configured AI provider for analysis. Shield keeps the result, not the message text.")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                        Button {
                            Task { await check() }
                        } label: {
                            HStack {
                                if isChecking { ProgressView() }
                                Text(isChecking ? "Checking…" : "Check message")
                            }
                            .frame(maxWidth: .infinity)
                        }
                        .buttonStyle(.borderedProminent)
                        .disabled(isChecking || text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || text.count > 10000)
                        if let error {
                            Text(error).font(.subheadline).foregroundStyle(.secondary)
                        }
                    }
                }
                if let result {
                    MessageResultCard(message: result, showAdvice: true)
                }
                SectionHeader(title: "Manual and SMS checks")
                if store.messages.isEmpty {
                    Card { Text("No texts checked yet").foregroundStyle(.secondary) }
                }
                ForEach(store.messages) { message in
                    MessageResultCard(message: message, showAdvice: false)
                }
            }
            .padding(16)
        }
        .background(Theme.canvas.ignoresSafeArea())
        .navigationTitle("Texts")
        .refreshable { await store.refresh() }
        .onAppear { store.markGatewayRead() }
    }

    @MainActor
    private func check() async {
        isChecking = true
        result = nil
        error = nil
        defer { isChecking = false }
        do {
            result = try await store.analyzeMessage(text)
            text = ""
        } catch {
            self.error = error.localizedDescription
        }
    }
}

private struct MessageResultCard: View {
    let message: MessageSummary
    let showAdvice: Bool

    private var tint: Color {
        switch message.label {
        case "likely_scam": Theme.danger
        case "suspicious": Theme.caution
        default: Theme.safe
        }
    }

    var body: some View {
        Card {
            VStack(alignment: .leading, spacing: 8) {
                CardLabel(title: message.title, symbol: "message", tint: tint)
                Text(message.source == "manual" ? "Pasted text" : "From \(message.sender)")
                    .font(.headline)
                Text(message.date, format: .dateTime.month().day().hour().minute())
                    .font(.subheadline).foregroundStyle(.secondary)
                if !message.flags.isEmpty {
                    Text(message.flags.map { Flag(id: $0).title }.joined(separator: ", "))
                        .font(.subheadline).foregroundStyle(.secondary)
                }
                if showAdvice {
                    Text(message.explanation ?? "Verify the sender independently before following instructions.")
                        .font(.subheadline).foregroundStyle(.secondary)
                }
                if message.textOnly {
                    Text("Only the text was checked. Attachments were not analyzed.")
                        .font(.footnote).foregroundStyle(.secondary)
                }
            }
        }
    }
}
