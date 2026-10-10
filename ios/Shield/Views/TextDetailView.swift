import SwiftUI

/// What the caregiver can do with a held text.
enum TextAction: String {
    case release
    case discard
}

/// One text sent to the protected number: what Palisade made of it, the text itself for as
/// long as the server keeps it, and, while it is held, the decision.
struct TextDetailView: View {
    let textID: String

    @Environment(ShieldStore.self) private var store
    @Environment(\.scenePhase) private var scenePhase
    @AppStorage(SettingsKey.seniorName) private var seniorName = SettingsKey.defaultSeniorName
    @State private var text: GatewayMessage?
    @State private var loadProblem: String?
    @State private var isWorking = false
    @State private var failure: Failure?

    /// A decision the server did not take, for the alert that says so.
    private struct Failure {
        let title: String
        let reason: String
    }

    /// This text's status in the page's list. When it changes there (a decision made here, a
    /// delivery report coming in), the text is fetched again.
    private var latestStatus: String? {
        store.gatewayMessages.first { $0.id == textID }?.status
    }

    var body: some View {
        // Looked at again as time passes: a text that runs out while it is on screen is put
        // away without anyone having to refresh.
        TimelineView(.periodic(from: .now, by: 30)) { timeline in
            content(at: timeline.date)
        }
        .navigationTitle(text?.needsReview == true ? "Held Text" : "Text")
        .inlineNavigationTitle()
        // A link in what may be a scam must not open, however the text is drawn.
        .environment(\.openURL, OpenURLAction { _ in .discarded })
        .alert(failure?.title ?? "", isPresented: isShowingFailure) {
            Button("OK") {}
        } message: {
            Text(failure?.reason ?? "")
        }
        .task(id: latestStatus) {
            await load()
        }
        .refreshable {
            await store.refresh()
            await load()
        }
        .onAppear {
            store.markTextRead(textID)
        }
        // The words of a text stay in the app no longer than they are being looked at.
        .onDisappear {
            text = nil
        }
        .onChange(of: scenePhase) { _, phase in
            if phase == .active {
                Task { await load() }
            } else {
                text = nil
            }
        }
    }

    @ViewBuilder
    private func content(at now: Date) -> some View {
        if let text {
            let isExpired = text.expiry <= now
            TextDetail(
                text: text,
                isExpired: isExpired,
                seniorName: seniorName,
                isForwarding: store.gatewayHealth?.forwardingEnabled == true
            )
            .safeAreaInset(edge: .bottom) {
                if text.needsReview, !isExpired {
                    DecisionBar(
                        text: text,
                        seniorName: seniorName,
                        isForwarding: store.gatewayHealth?.forwardingEnabled == true
                    ) { action in
                        Task { await decide(action) }
                    }
                    .disabled(isWorking)
                }
            }
        } else {
            ZStack {
                Theme.canvas.ignoresSafeArea()
                if let loadProblem {
                    ContentUnavailableView {
                        Label("Couldn’t Load This Text", systemImage: "exclamationmark.triangle")
                    } description: {
                        Text(loadProblem)
                    } actions: {
                        Button("Try Again") {
                            Task { await load() }
                        }
                        .cardButton()
                    }
                } else {
                    ProgressView()
                }
            }
        }
    }

    private var isShowingFailure: Binding<Bool> {
        Binding {
            failure != nil
        } set: { isShown in
            if !isShown { failure = nil }
        }
    }

    private func load() async {
        do {
            let loaded = try await store.gatewayDetail(textID)
            // Not drawn while the app is behind something else, which is when iOS takes its
            // picture of the app for the app switcher.
            guard !Task.isCancelled, scenePhase == .active else { return }
            text = loaded
            loadProblem = nil
        } catch {
            if !Task.isCancelled { loadProblem = error.localizedDescription }
        }
    }

    private func decide(_ action: TextAction) async {
        isWorking = true
        defer { isWorking = false }
        do {
            try await store.decideGatewayMessage(textID, action: action.rawValue)
        } catch {
            failure = Failure(
                title: action == .release ? "Couldn’t Release the Text" : "Couldn’t Discard the Text",
                reason: error.localizedDescription
            )
        }
        // Either way, show where the text stands now.
        await load()
    }
}

// MARK: - The page

private struct TextDetail: View {
    let text: GatewayMessage
    let isExpired: Bool
    let seniorName: String
    /// False while the server keeps checked texts instead of sending them on.
    let isForwarding: Bool

    var body: some View {
        List {
            Section {
                TextHeader(text: text)
                    .padding(.vertical, 5)
            } footer: {
                Text(standing)
            }
            if let words = text.text, !words.isEmpty, !isExpired {
                Section {
                    // Verbatim: nothing in it is read as formatting or turned into a link.
                    Text(verbatim: words)
                } header: {
                    Text("The Text")
                } footer: {
                    Text("Links are switched off here. Nobody has checked who the sender is or where a link leads.")
                }
            }
            if !text.signs.isEmpty {
                Section("What Palisade Noticed") {
                    ForEach(text.signs) { sign in
                        Text(sign.title)
                    }
                }
            }
            if showsAdvice {
                Section("What Palisade Advises") {
                    Text(text.explanation)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            Section {
            } footer: {
                Label("Palisade keeps a text encrypted for up to 24 hours, so you can review it, then deletes it. What it detected is kept, not what was said.", systemImage: "lock.fill")
            }
        }
        .groupedListStyle()
        .headerProminence(.increased)
    }

    /// The server's advice is about what a text asks for, so an ordinary text gets none. Nor
    /// does one held for what it is (an attachment, its length): the line under the header
    /// already says why.
    private var showsAdvice: Bool {
        switch text.hold {
        case .attachment, .tooLong, .oldRecord: false
        case .scamSigns, .notChecked: !text.explanation.isEmpty
        case nil: text.verdict != .ordinary && !text.explanation.isEmpty
        }
    }

    /// Where the text stands and what happens next, in a sentence.
    private var standing: String {
        switch text.state {
        case .held:
            if isExpired { return "Nobody decided within 24 hours, so it was deleted without being forwarded." }
            switch text.hold {
            case .attachment:
                return "A text with a picture or a file cannot be forwarded in this version. It can only be discarded."
            case .tooLong:
                return "With Palisade’s note under it, this text is too long to forward. It can only be discarded."
            case .oldRecord:
                return "This is a record from an earlier setup. It can only be discarded."
            case .scamSigns, .notChecked, nil:
                let until = text.expiry.formatted(.dateTime.month().day().hour().minute())
                return "Palisade is holding this text until \(until). If nobody decides by then, it is deleted without being forwarded."
            }
        case .pending:
            return "Palisade is checking this text."
        case .queued:
            return isForwarding
                ? "It is about to be forwarded to \(seniorName)."
                : "It will be forwarded to \(seniorName) once forwarding is turned on at the server."
        case .sending:
            return "It is being forwarded to \(seniorName)."
        case .sent:
            return "The phone company has accepted it. That is not yet proof that it arrived."
        case .delivered:
            return "The phone company reports it delivered. That does not mean \(seniorName) has read it."
        case .deliveryFailed:
            return "It could not be delivered. Palisade does not try again by itself."
        case .deliveryUnknown:
            return "Palisade could not confirm that it was sent. Ask \(seniorName) before sending it again."
        case .discarded:
            return "It was discarded. Nothing was forwarded to \(seniorName)."
        case .expired:
            return "Nobody decided within 24 hours, so it was deleted without being forwarded."
        case .unknown:
            return "This version of the app does not know this text’s status."
        }
    }
}

/// What the text is, who sent it, when, and how risky it looked.
private struct TextHeader: View {
    let text: GatewayMessage

    var body: some View {
        let look = OutcomeLook(text)
        VStack(alignment: .leading, spacing: 6) {
            CardLabel(title: look.title, symbol: look.symbol, tint: look.tint)
            // The whole number, which only this page is given. Until it is, the masked one.
            Text(verbatim: text.originalSender.map(phoneNumber) ?? senderDescription(text.sender))
                .font(.title2.weight(.bold))
            VStack(alignment: .leading, spacing: 2) {
                Text(text.date, format: .dateTime.month().day().year().hour().minute())
                if let risk = text.risk {
                    Text("Risk \(risk) out of 100")
                }
            }
            .font(.subheadline)
            .foregroundStyle(.secondary)
        }
        .accessibilityElement(children: .combine)
    }
}

/// The decision on a held text. Pinned under the page so it never scrolls out of reach.
/// Neither choice can be taken back, so each is asked once before it is carried out.
private struct DecisionBar: View {
    let text: GatewayMessage
    let seniorName: String
    /// False while the server keeps checked texts instead of sending them on.
    let isForwarding: Bool
    let decide: (TextAction) -> Void

    @State private var isAskingToRelease = false
    @State private var isAskingToDiscard = false

    var body: some View {
        // Side by side at one width while both labels fit; stacked for a long name or large text.
        ViewThatFits(in: .horizontal) {
            EqualWidthHStack(spacing: 10) { buttons }
            VStack(spacing: 8) { buttons }
        }
        .controlSize(.large)
        .padding(.horizontal, 16)
        .padding(.top, 10)
        .padding(.bottom, 8)
        .background(Theme.canvas)
    }

    @ViewBuilder private var buttons: some View {
        // False for a text that can only be discarded (an attachment, too long to forward).
        if text.reviewEligible {
            Button {
                isAskingToRelease = true
            } label: {
                Text("Release to \(seniorName)")
                    .lineLimit(1)
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .confirmationDialog(
                "Release This Text to \(seniorName)?", isPresented: $isAskingToRelease, titleVisibility: .visible
            ) {
                Button("Release to \(seniorName)") { decide(.release) }
            } message: {
                Text(whatReleasingDoes)
            }
        }
        Button {
            isAskingToDiscard = true
        } label: {
            Text("Discard")
                .lineLimit(1)
                .frame(maxWidth: .infinity)
        }
        .quietButton()
        .confirmationDialog("Discard This Text?", isPresented: $isAskingToDiscard, titleVisibility: .visible) {
            Button("Discard", role: .destructive) { decide(.discard) }
        } message: {
            Text("It is deleted now and cannot be brought back. Nothing is sent to \(seniorName).")
        }
    }

    /// Why Palisade held the text, said again at the moment it is about to be let through.
    private var whatReleasingDoes: String {
        let why = switch (text.hold, text.verdict) {
        case (.notChecked, _): "Palisade could not check this text."
        case (_, .likelyScam): "Palisade held this text as a likely scam."
        default: "Palisade held this text as suspicious."
        }
        let then = isForwarding
            ? "It will be sent to \(seniorName)’s phone"
            : "It will be sent to \(seniorName)’s phone once forwarding is turned on at the server"
        return "\(why) \(then), with a note that you released it."
    }
}
