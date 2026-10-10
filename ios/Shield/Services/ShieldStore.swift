import Foundation
import Observation

struct CallStats: Equatable {
    var screened = 0
    var blocked = 0
    var warned = 0
}

/// The app's pages, one per tab.
enum AppTab: Hashable {
    case home
    case calls
    case signs
}

/// The app's state: the connection to the Palisade server, the call in progress, and recent calls.
@Observable
@MainActor
final class ShieldStore {
    enum Connection: Equatable {
        case notConfigured
        case connecting
        case connected
        /// The server asks each caregiver to sign in, and nobody is signed in on this phone.
        case signedOut
        /// The server turned down whoever is signed in. Why, in a sentence.
        case refused(String)
        case failed(String)
    }

    private(set) var connection: Connection = .connecting
    /// True when the server asks each caregiver to sign in, in place of the Access Token in Settings.
    private(set) var offersSignIn = false
    /// Who is signed in on this phone: their email. Nil when nobody is.
    private(set) var account: String?
    /// True while a sign-in or a sign-out is under way, so that neither is started twice.
    private(set) var isSigningInOrOut = false
    /// The call in progress, or the one that just ended.
    private(set) var call: LiveCall?
    /// The call shown full screen. Set when a call starts; cleared when the caregiver closes it.
    private(set) var presentedCall: LiveCall?
    /// What Palisade just did on the call in progress, for the buzz that goes with it. History
    /// replayed when the app connects never sets it.
    private(set) var latestAction: ShieldAction?
    /// The page showing under the call screen.
    var tab = AppTab.home
    /// Call summaries pushed onto the Summary page's navigation stack, and onto the Calls page's.
    var homePath: [CallSummary] = []
    var callsPath: [CallSummary] = []
    private(set) var history: [CallSummary] = [] {
        didSet { stats = Self.stats(for: history) }
    }
    private(set) var stats = CallStats()
    private(set) var demoScripts: [DemoScript] = []
    private(set) var warnThreshold = 60
    private(set) var endThreshold = 85
    /// True when the server only shows what it would do and leaves real calls alone.
    private(set) var isPracticeMode = false
    private(set) var demoError: String?
    var isShowingDemoError: Bool {
        get { demoError != nil }
        set { if !newValue { demoError = nil } }
    }

    /// The server as Settings describes it: its address, and the Access Token if one was typed.
    @ObservationIgnored private var api: ShieldAPI?
    /// The caregiver's sign-in, when the server asks for one.
    @ObservationIgnored private var session: SignIn?
    /// False for a store of fixed sample data, which must never be replaced by a live feed.
    @ObservationIgnored private var followsSettings = true
    @ObservationIgnored private var feedTask: Task<Void, Never>?
    @ObservationIgnored private var settleTask: Task<Void, Never>?

    /// Events older than this when we connect are history: they rebuild state but never pop up the call screen.
    private static let freshness: TimeInterval = 10

    /// Starts (or restarts) the connection using the address and token from Settings.
    func connect() {
        feedTask?.cancel()
        settleTask?.cancel()
        call = nil
        presentedCall = nil
        latestAction = nil
        guard let api = ShieldAPI.fromSettings else {
            self.api = nil
            forgetSignIn()
            connection = .notConfigured
            return
        }
        // Another server: what the last one said about signing in no longer holds.
        if api.baseURL != self.api?.baseURL { forgetSignIn() }
        self.api = api
        connection = .connecting
        feedTask = Task { await self.follow(api) }
    }

    /// Reconnects only if the address or token in Settings differs from the one in use.
    func connectIfSettingsChanged() {
        if followsSettings, ShieldAPI.fromSettings != api { connect() }
    }

    func refresh() async {
        guard let api = await currentAPI() else { return }
        await loadServerInfo(api)
    }

    /// Opens Auth0's sign-in page and, once someone has signed in, connects as them. Answers
    /// what went wrong, in a sentence. Nil when it worked, or when they closed the page themselves.
    func signIn() async -> String? {
        guard let session, !isSigningInOrOut else { return nil }
        isSigningInOrOut = true
        defer { isSigningInOrOut = false }
        do {
            try await session.signIn()
        } catch is CancellationError {
            return nil
        } catch {
            return error.localizedDescription
        }
        connect()
        return nil
    }

    /// Signs the caregiver out of this phone, which stops showing calls at once.
    func signOut() async {
        guard let session, !isSigningInOrOut else { return }
        isSigningInOrOut = true
        defer { isSigningInOrOut = false }
        await session.signOut()
        connect()
    }

    func showCall() {
        if let call, !call.hasEnded { presentedCall = call }
    }

    func closeCall() {
        presentedCall = nil
    }

    /// Leaves the call screen and opens the summary of that call, on the Calls page.
    func showSummary(of callID: String) async {
        if !history.contains(where: { $0.id == callID }) {
            await reloadHistory()
        }
        presentedCall = nil
        if let summary = history.first(where: { $0.id == callID }) {
            tab = .calls
            callsPath = [summary]
        }
    }

    func startDemoCall(_ script: DemoScript) async {
        guard let api = await currentAPI() else { return }
        do {
            try await api.startDemoCall(scriptID: script.id)
            demoError = nil
        } catch {
            demoError = Self.describe(error)
        }
    }

    // MARK: - Feed

    private func follow(_ server: ShieldAPI) async {
        var retryDelay = 1.0
        while !Task.isCancelled {
            do {
                // Asked on every attempt: the server may have had sign-in switched on or off
                // since the last one.
                try await askAboutSignIn(server)
                guard let api = try await authorized(server) else {
                    // Signing in connects again, so there is nothing to keep trying here.
                    lockOut(.signedOut)
                    return
                }
                await loadServerInfo(api)
                var connectedAt = Date.now
                for try await item in api.events() {
                    switch item {
                    case .connected:
                        connectedAt = .now
                        connection = .connected
                        retryDelay = 1
                        call = nil
                        settleAfterReplay()
                    case .event(let event):
                        handle(event, isFresh: event.date > connectedAt.addingTimeInterval(-Self.freshness))
                    }
                }
                connection = .connecting  // the server closed the feed; reconnect
            } catch {
                if Task.isCancelled { return }
                switch error {
                case ShieldAPIError.refused(let reason):
                    lockOut(.refused(reason))
                case ShieldAPIError.unauthorized where session != nil:
                    // A token Auth0 had just handed over: the server and Auth0 are set up differently.
                    lockOut(.refused("The server did not accept this sign-in."))
                default:
                    connection = .failed(Self.describe(error))
                }
            }
            // A refusal is tried again too: being put on the server's list is what ends it.
            try? await Task.sleep(for: .seconds(retryDelay))
            retryDelay = min(retryDelay * 2, 8)
        }
    }

    // MARK: - Sign-in

    /// Asks the server whether it wants a sign-in, and with which Auth0 tenant.
    private func askAboutSignIn(_ server: ShieldAPI) async throws {
        let config = try await server.signInConfig()
        try Task.checkCancellation()
        // The same `SignIn` for as long as the answer is the same (see `SignIn`).
        if session?.config != config { session = SignIn(config) }
        offersSignIn = session != nil
        account = session?.account
    }

    /// The server, asked as someone it lets in: the signed-in caregiver where it wants a
    /// sign-in, whoever holds the Access Token from Settings where it does not. Nil when it
    /// wants a sign-in and nobody is signed in.
    private func authorized(_ server: ShieldAPI) async throws -> ShieldAPI? {
        guard let session else { return server }
        let token = try await session.accessToken()
        try Task.checkCancellation()
        guard let token else {
            account = nil  // a sign-in that has run out is over
            return nil
        }
        return server.with(token: token)
    }

    /// The server to ask right now, with a token that has not run out. Nil when there is no
    /// server, or it wants a sign-in and nobody is signed in.
    private func currentAPI() async -> ShieldAPI? {
        guard let api else { return nil }
        return try? await authorized(api)
    }

    /// Nobody on this phone may watch calls: put away what the last person to watch could see.
    private func lockOut(_ state: Connection) {
        connection = state
        call = nil
        presentedCall = nil
        history = []
        demoScripts = []
    }

    private func forgetSignIn() {
        session = nil
        offersSignIn = false
        account = nil
    }

    private func handle(_ event: ShieldEvent, isFresh: Bool) {
        guard let callID = event.callSID else { return }
        if event.type == "call_started" {
            let started = LiveCall(
                id: callID,
                caller: event.caller ?? "Unknown",
                isDemo: event.simulated ?? false,
                startedAt: event.date
            )
            call = started
            // Also swap in the rebuilt call when the screen is already showing it (we reconnected mid-call).
            if isFresh || presentedCall?.id == callID { presentedCall = started }
            return
        }
        guard let call, call.id == callID else { return }
        call.apply(event)
        if event.type == "action", isFresh {
            latestAction = event.kind == ShieldAction.Kind.end.rawValue ? call.ending : call.warning
            // Palisade stepping in is what the caregiver has the app for: show it, even if they
            // had put the call screen away.
            if presentedCall == nil { presentedCall = call }
        }
        if event.type == "call_ended", isFresh {
            Task { await self.reloadHistory() }
        }
    }

    /// The server replays recent events when we connect. Once they are in, show a call that is
    /// still going (the app was opened mid-call) and forget one that ended before we arrived.
    private func settleAfterReplay() {
        settleTask?.cancel()
        settleTask = Task {
            try? await Task.sleep(for: .milliseconds(800))
            guard !Task.isCancelled, let call = self.call else { return }
            if call.hasEnded {
                if self.presentedCall == nil { self.call = nil }
            } else if self.presentedCall == nil {
                self.presentedCall = call
            }
        }
    }

    private func loadServerInfo(_ api: ShieldAPI) async {
        if let health = try? await api.health() {
            warnThreshold = health.warnThreshold ?? warnThreshold
            endThreshold = health.endThreshold ?? endThreshold
            isPracticeMode = health.actions == "dry_run"
        }
        demoScripts = (try? await api.demoScripts()) ?? []
        await loadHistory(api)
    }

    private func loadHistory(_ api: ShieldAPI) async {
        if let calls = try? await api.calls() {
            history = calls
        }
    }

    private func reloadHistory() async {
        if let api = await currentAPI() { await loadHistory(api) }
    }

    private static func stats(for calls: [CallSummary]) -> CallStats {
        var stats = CallStats()
        for call in calls where !call.isDemo && call.outcome != .inProgress {
            stats.screened += 1
            if call.outcome == .endedByShield { stats.blocked += 1 }
            if call.outcome == .warned { stats.warned += 1 }
        }
        return stats
    }

    private static func describe(_ error: Error) -> String {
        if let apiError = error as? ShieldAPIError { return apiError.localizedDescription }
        if let urlError = error as? URLError {
            switch urlError.code {
            case .cannotConnectToHost, .cannotFindHost, .timedOut, .networkConnectionLost, .notConnectedToInternet:
                return "Can’t reach the Palisade server."
            default:
                break
            }
        }
        return "Something went wrong talking to the Palisade server."
    }
}

#if DEBUG
extension ShieldStore {
    /// A store frozen in a given state, for previews. It never connects to a server.
    static func preview(
        connection: Connection = .connected,
        call: LiveCall? = nil,
        isShowingCall: Bool = false,
        history: [CallSummary] = [],
        demoScripts: [DemoScript] = [],
        isPracticeMode: Bool = false,
        offersSignIn: Bool = false,
        account: String? = nil
    ) -> ShieldStore {
        let store = ShieldStore()
        store.followsSettings = false
        store.connection = connection
        store.offersSignIn = offersSignIn
        store.account = account
        store.isPracticeMode = isPracticeMode
        store.call = call
        store.presentedCall = isShowingCall ? call : nil
        store.history = history
        store.demoScripts = demoScripts
        return store
    }
}
#endif
