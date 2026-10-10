import SwiftUI

/// Checks a text the caregiver pastes in. It goes to the server to be scored and nowhere
/// else: nothing is held, and nothing is sent to anyone.
struct CheckTextView: View {
    @Environment(ShieldStore.self) private var store
    @Environment(\.dismiss) private var dismiss
    @State private var text = ""
    @State private var isChecking = false
    @State private var result: MessageSummary?
    @State private var problem: String?
    @FocusState private var isTyping: Bool

    /// The longest text the server takes.
    private static let longest = 10_000

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextEditor(text: $text)
                        .frame(minHeight: 140)
                        .focused($isTyping)
                        .accessibilityLabel("Text to check")
                        .overlay(alignment: .topLeading) {
                            if text.isEmpty {
                                // Where the editor puts its first line.
                                Text("Paste a text here")
                                    .foregroundStyle(.tertiary)
                                    .padding(.top, 8)
                                    .padding(.leading, 5)
                                    .allowsHitTesting(false)
                                    .accessibilityHidden(true)
                            }
                        }
                } footer: {
                    Text("The text goes to your Palisade server and the AI service it uses, to be checked. Only the result is kept.")
                }
                if let result {
                    CheckResult(check: result)
                }
                if let problem {
                    Section {
                    } footer: {
                        Text(problem)
                    }
                }
            }
            .formStyle(.grouped)
            .headerProminence(.increased)
            .animation(Motion.change, value: result?.id)
            .navigationTitle("Check a Text")
            .inlineNavigationTitle()
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Close") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Check") {
                        Task { await check() }
                    }
                    .disabled(!canCheck)
                }
            }
        }
        .onAppear {
            isTyping = true
        }
        .onChange(of: store.presentedCall?.id) {
            // A call just started: get out of its way.
            if store.presentedCall != nil { dismiss() }
        }
    }

    private var canCheck: Bool {
        !isChecking && text.count <= Self.longest && !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    private func check() async {
        isChecking = true
        isTyping = false
        problem = nil
        defer { isChecking = false }
        do {
            result = try await store.analyzeMessage(text)
        } catch {
            result = nil
            problem = "The text could not be checked. \(error.localizedDescription)"
        }
    }
}

/// What the check found: the verdict, the server's advice, and the signs it saw.
private struct CheckResult: View {
    let check: MessageSummary

    var body: some View {
        let look = OutcomeLook(check)
        Section("Result") {
            VStack(alignment: .leading, spacing: 6) {
                // A text with nothing wrong reports no state, so its label is gray and bare.
                CardLabel(title: look.title, symbol: look.isNotable ? look.symbol : nil, tint: look.isNotable ? look.tint : .secondary)
                Text(check.explanation ?? "Check with the sender through a number you trust before doing what the text asks.")
                    .fixedSize(horizontal: false, vertical: true)
            }
            .padding(.vertical, 5)
            .accessibilityElement(children: .combine)
        }
        if !check.signs.isEmpty {
            Section("What Palisade Noticed") {
                ForEach(check.signs) { sign in
                    Text(sign.title)
                }
            }
        }
    }
}
