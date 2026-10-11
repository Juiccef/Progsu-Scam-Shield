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
    case alerts
    case calls
    case texts
    case signs
}

/// What a row leads to: one call's summary, or one text. The text is named by its ID, and its
/// words are fetched when its page opens, so they are never kept in a list.
enum Page: Hashable {
    case call(CallSummary)
    case text(String)
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
    /// The server's address, once it has said it has a privacy policy and a support page to
    /// show. Nil for a server from before those pages, so that Settings offers no link to a
    /// page that is not there.
    private(set) var publicPages: URL?
    /// The call in progress, or the one that just ended.
    private(set) var call: LiveCall?
    /// The call shown full screen. Set when a call starts; cleared when the caregiver closes it.
    private(set) var presentedCall: LiveCall?
    /// What Palisade just did on the call in progress, for the buzz that goes with it. History
    /// replayed when the app connects never sets it.
    private(set) var latestAction: ShieldAction?
    /// The page showing under the call screen.
    var tab = AppTab.home
    /// What is pushed onto each page's navigation stack.
    var homePath: [Page] = []
    var callsPath: [CallSummary] = []
    var textsPath: [Page] = []
    private(set) var history: [CallSummary] = [] {
        didSet { stats = Self.stats(for: history) }
    }
    private(set) var stats = CallStats()
    /// Results of texts checked one at a time: those pasted into the app and, on a server
    /// with text protection off, those sent to its number.
    private(set) var messages: [MessageSummary] = []
    /// Texts sent to the protected number, newest first: held, waiting, sent on, discarded.
    private(set) var gatewayMessages: [GatewayMessage] = []
    /// What the server says about text protection. Nil until it has answered.
    private(set) var gatewayHealth: GatewayHealth?
    /// The protected number, the one people text.
    private(set) var shieldNumber = ""
    private(set) var gatewayError: String?
    /// The text Palisade just held, for the buzz that goes with it. Replayed history never
    /// sets it, nor does a text that has already been looked at.
    var latestHeldText: String?
    /// Held texts nobody has looked at yet: the count on the Texts tab.
    private(set) var unreadGatewayIDs: Set<String> = []
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
    /// Held texts that have been looked at, remembered for each server between launches.
    @ObservationIgnored private var seenGatewayIDs: [String] = []
    /// Held texts that have already had their buzz.
    @ObservationIgnored private var alertedGatewayIDs: Set<String> = []
    /// Whole texts for a store of fixed sample data, which has no server to fetch them from.
    @ObservationIgnored private var sampleTexts: [String: GatewayMessage] = [:]
    private var gatewaySeenKey: String { "gatewaySeen.\(api?.baseURL.absoluteString ?? "")" }

    /// Events older than this when we connect are history: they rebuild state but never pop up the call screen.
    private static let freshness: TimeInterval = 10

    /// Starts (or restarts) the connection using the address and token from Settings.
    func connect() {
        feedTask?.cancel()
        settleTask?.cancel()
        call = nil
        presentedCall = nil
        latestAction = nil
        forgetTexts()
        guard let api = ShieldAPI.fromSettings else {
            self.api = nil
            forgetSignIn()
            publicPages = nil
            connection = .notConfigured
            return
        }
        // Another server: what the last one said about signing in, and about its pages, no
        // longer holds.
        if api.baseURL != self.api?.baseURL {
            forgetSignIn()
            publicPages = nil
        }
        self.api = api
        seenGatewayIDs = UserDefaults.standard.stringArray(forKey: gatewaySeenKey) ?? []
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

    func analyzeMessage(_ text: String) async throws -> MessageSummary {
        guard let api = await currentAPI() else { throw ShieldAPIError.notAShieldServer }
        let result = try await api.analyzeMessage(text)
        await loadMessages(api)
        return result
    }

    /// One text with its words and its sender's whole number, for as long as the server keeps them.
    func gatewayDetail(_ id: String) async throws -> GatewayMessage {
        if !followsSettings, let sample = sampleTexts[id] { return sample }
        guard let api = await currentAPI() else { throw ShieldAPIError.notAShieldServer }
        return try await api.gatewayDetail(id)
    }

    /// Releases a held text to the protected person, or discards it. `action` is the server's word.
    func decideGatewayMessage(_ id: String, action: String) async throws {
        #if DEBUG
        if !followsSettings {
            decideSample(id, action: action)
            return
        }
        #endif
        guard let api = await currentAPI() else { throw ShieldAPIError.notAShieldServer }
        _ = try await api.gatewayDecision(id, action: action)
        await loadGateway(api)
    }

    /// The Texts tab was chosen, or its list came back into view.
    func textsCameIntoView() {
        if isShowingTexts { markGatewayRead() }
    }

    /// True while the list of texts itself is on screen: its tab is chosen, no text is open
    /// on top of it, and no call screen covers it.
    private var isShowingTexts: Bool {
        tab == .texts && textsPath.isEmpty && presentedCall == nil
    }

    /// Every held text has now been seen.
    func markGatewayRead() {
        for message in gatewayMessages where message.needsReview && !seenGatewayIDs.contains(message.id) {
            seenGatewayIDs.append(message.id)
        }
        rememberSeen()
        unreadGatewayIDs = []
    }

    /// One held text was opened, from whichever page.
    func markTextRead(_ id: String) {
        guard unreadGatewayIDs.remove(id) != nil else { return }
        seenGatewayIDs.append(id)
        rememberSeen()
    }

    private func rememberSeen() {
        seenGatewayIDs = Array(seenGatewayIDs.suffix(2000))
        UserDefaults.standard.set(seenGatewayIDs, forKey: gatewaySeenKey)
    }

    func showCall() {
        if let call, !call.hasEnded { presentedCall = call }
    }

    func closeCall() {
        presentedCall = nil
        // A text held while the call screen covered the list has only now been seen.
        textsCameIntoView()
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

    /// Says that Palisade got a finished call wrong, or takes that back. It is a mark on the
    /// call and no more than that.
    func markCall(_ id: String, wrong: Bool) async throws {
        #if DEBUG
        if !followsSettings {
            markSample(id, wrong: wrong)
            return
        }
        #endif
        guard let api = await currentAPI() else { throw ShieldAPIError.notAShieldServer }
        let marked = try await api.markCall(id, wrong: wrong)
        if wants(api), let index = history.firstIndex(where: { $0.id == id }) {
            history[index] = marked
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
                // Before the sign-in is looked at: the pages are for someone signed out too.
                await askAboutPages(server)
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

    /// Asks the server whether it has its privacy policy and its support page. Asked on every
    /// attempt, as the sign-in is: a server gains them when it is brought up to date.
    private func askAboutPages(_ server: ShieldAPI) async {
        let hasThem = await server.hasPublicPages()
        guard !Task.isCancelled, api?.baseURL == server.baseURL else { return }
        publicPages = hasThem ? server.baseURL : nil
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
        forgetTexts()
    }

    /// Texts belong to whoever may watch: none of it stays when the connection is started
    /// over, or when nobody on this phone is let in.
    private func forgetTexts() {
        messages = []
        gatewayMessages = []
        gatewayHealth = nil
        shieldNumber = ""
        gatewayError = nil
        latestHeldText = nil
        textsPath = []
        unreadGatewayIDs = []
        alertedGatewayIDs = []
    }

    private func forgetSignIn() {
        session = nil
        offersSignIn = false
        account = nil
    }

    private func handle(_ event: ShieldEvent, isFresh: Bool) {
        if event.type == "gateway_message_updated" {
            if let id = event.messageID, event.status == "held", isFresh,
               !seenGatewayIDs.contains(id), alertedGatewayIDs.insert(id).inserted {
                latestHeldText = id
            }
            Task { await self.reloadGateway() }
            return
        }
        if event.type == "message_scored" {
            if isFresh { Task { await self.reloadMessages() } }
            return
        }
        if event.type == "call_marked" {
            // A mark is on a call that is over, so the list of calls is what changed. This also
            // brings in a mark made on another phone.
            if isFresh { Task { await self.reloadHistory() } }
            return
        }
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
            guard wants(api) else { return }
            gatewayHealth = health.gateway
            warnThreshold = health.warnThreshold ?? warnThreshold
            endThreshold = health.endThreshold ?? endThreshold
            isPracticeMode = health.actions == "dry_run"
        }
        demoScripts = (try? await api.demoScripts()) ?? []
        await loadHistory(api)
        await loadMessages(api)
        if gatewayHealth?.enabled == true { await loadGateway(api) }
    }

    private func loadGateway(_ api: ShieldAPI) async {
        do {
            let list = try await api.gatewayMessages()
            guard wants(api) else { return }
            gatewayMessages = list.messages
            shieldNumber = list.shieldNumber
            gatewayError = nil
            // A pending record has not raised a review alert yet. Remember only
            // held records so delayed analysis can still trigger its first banner.
            alertedGatewayIDs.formUnion(list.messages.filter(\.needsReview).map(\.id))
            unreadGatewayIDs = Set(list.messages.filter { $0.needsReview && !seenGatewayIDs.contains($0.id) }.map(\.id))
            if isShowingTexts { markGatewayRead() }
        } catch {
            if wants(api) { gatewayError = Self.describe(error) }
        }
    }

    private func loadMessages(_ api: ShieldAPI) async {
        if let messages = try? await api.messages(), wants(api) {
            self.messages = messages
        }
    }

    private func loadHistory(_ api: ShieldAPI) async {
        if let calls = try? await api.calls() {
            history = calls
        }
    }

    private func reloadHistory() async {
        if let api = await currentAPI() { await loadHistory(api) }
    }

    private func reloadGateway() async {
        if let api = await currentAPI() { await loadGateway(api) }
    }

    private func reloadMessages() async {
        if let api = await currentAPI() { await loadMessages(api) }
    }

    /// True while an answer from `api` is still wanted: Settings still points at that server,
    /// and whoever is on this phone is still let in.
    private func wants(_ api: ShieldAPI) -> Bool {
        guard self.api?.baseURL == api.baseURL else { return false }
        switch connection {
        case .signedOut, .refused: return false
        case .notConfigured, .connecting, .connected, .failed: return true
        }
    }

    private static func stats(for calls: [CallSummary]) -> CallStats {
        var stats = CallStats()
        for call in calls where !call.isDemo && call.outcome != .inProgress {
            stats.screened += 1
            // A call the caregiver marked as not a scam is not counted as a scam stopped.
            if call.outcome == .endedByShield, call.markedWrong != true { stats.blocked += 1 }
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
        // Sample data has no server. A made-up address, so that Settings shows its two links.
        store.publicPages = URL(string: "https://palisade.example")
        return store
    }

    /// Gives a preview store fixed texts. `whole` are the same texts as the page for one text
    /// is given them, with their words.
    func withTexts(
        _ texts: [GatewayMessage] = [],
        whole: [GatewayMessage] = [],
        checks: [MessageSummary] = [],
        protection: GatewayHealth? = nil,
        number: String = ""
    ) -> ShieldStore {
        gatewayMessages = texts
        sampleTexts = Dictionary(uniqueKeysWithValues: whole.map { ($0.id, $0) })
        messages = checks
        gatewayHealth = protection
        shieldNumber = number
        unreadGatewayIDs = Set(texts.filter(\.needsReview).map(\.id))
        return self
    }

    /// What a mark does to a sample call, so the page can be tried with no server.
    private func markSample(_ id: String, wrong: Bool) {
        guard let index = history.firstIndex(where: { $0.id == id }) else { return }
        history[index].markedWrong = wrong
    }

    /// What a decision does to a sample text, so the pages can be tried with no server.
    private func decideSample(_ id: String, action: String) {
        guard let index = gatewayMessages.firstIndex(where: { $0.id == id }) else { return }
        gatewayMessages[index] = gatewayMessages[index].sampleDecided(action)
        sampleTexts[id] = sampleTexts[id]?.sampleDecided(action)
        unreadGatewayIDs.remove(id)
    }
}
#endif
