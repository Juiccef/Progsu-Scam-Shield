import Foundation
import Observation

struct CallStats: Equatable {
    var screened = 0
    var blocked = 0
    var warned = 0
}

/// The app's state: the connection to the Shield server, the call in progress, and recent calls.
@Observable
@MainActor
final class ShieldStore {
    enum Tab { case home, texts }
    var selectedTab: Tab = .home
    enum Connection: Equatable {
        case notConfigured
        case connecting
        case connected
        case failed(String)
    }

    private(set) var connection: Connection = .connecting
    /// The call in progress, or the one that just ended.
    private(set) var call: LiveCall?
    /// The call shown full screen. Set when a call starts; cleared when the caregiver closes it.
    private(set) var presentedCall: LiveCall?
    /// Call summaries pushed onto the home screen's navigation stack.
    var path: [CallSummary] = []
    private(set) var history: [CallSummary] = [] {
        didSet { stats = Self.stats(for: history) }
    }
    private(set) var stats = CallStats()
    private(set) var messages: [MessageSummary] = []
    private(set) var gatewayMessages: [GatewayMessage] = []
    private(set) var gatewayHealth: GatewayHealth?
    private(set) var shieldNumber = ""
    private(set) var gatewayError: String?
    var gatewayBannerID: String?
    var gatewayDetailID: String?
    private(set) var unreadGatewayIDs: Set<String> = []
    private(set) var demoScripts: [DemoScript] = []
    private(set) var warnThreshold = 60
    private(set) var endThreshold = 85
    private(set) var demoError: String?
    var isShowingDemoError: Bool {
        get { demoError != nil }
        set { if !newValue { demoError = nil } }
    }

    @ObservationIgnored private var api: ShieldAPI?
    /// False for a store of fixed sample data, which must never be replaced by a live feed.
    @ObservationIgnored private var followsSettings = true
    @ObservationIgnored private var feedTask: Task<Void, Never>?
    @ObservationIgnored private var settleTask: Task<Void, Never>?
    @ObservationIgnored private var seenGatewayIDs: [String] = []
    @ObservationIgnored private var alertedGatewayIDs: Set<String> = []
    private var gatewaySeenKey: String { "gatewaySeen.\(api?.baseURL.absoluteString ?? "")" }

    /// Events older than this when we connect are history: they rebuild state but never pop up the call screen.
    private static let freshness: TimeInterval = 10

    /// Starts (or restarts) the connection using the address and token from Settings.
    func connect() {
        feedTask?.cancel()
        settleTask?.cancel()
        call = nil
        presentedCall = nil
        messages = []
        gatewayMessages = []
        gatewayHealth = nil
        gatewayError = nil
        gatewayBannerID = nil
        gatewayDetailID = nil
        unreadGatewayIDs = []
        alertedGatewayIDs = []
        guard let api = ShieldAPI.fromSettings else {
            self.api = nil
            connection = .notConfigured
            return
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
        guard let api else { return }
        await loadServerInfo(api)
    }

    func analyzeMessage(_ text: String) async throws -> MessageSummary {
        guard let api else { throw ShieldAPIError.notAShieldServer }
        let result = try await api.analyzeMessage(text)
        await loadMessages(api)
        return result
    }

    func gatewayDetail(_ id: String) async throws -> GatewayMessage {
        guard let api else { throw ShieldAPIError.notAShieldServer }
        return try await api.gatewayDetail(id)
    }

    func decideGatewayMessage(_ id: String, action: String) async throws {
        guard let api else { throw ShieldAPIError.notAShieldServer }
        _ = try await api.gatewayDecision(id, action: action)
        await loadGateway(api)
    }

    func markGatewayRead() {
        for message in gatewayMessages where message.needsReview && !seenGatewayIDs.contains(message.id) {
            seenGatewayIDs.append(message.id)
        }
        seenGatewayIDs = Array(seenGatewayIDs.suffix(2000))
        UserDefaults.standard.set(seenGatewayIDs, forKey: gatewaySeenKey)
        unreadGatewayIDs = []
    }

    func showCall() {
        if let call, !call.hasEnded { presentedCall = call }
    }

    func closeCall() {
        presentedCall = nil
    }

    /// Leaves the call screen and opens the summary of that call.
    func showSummary(of callID: String) async {
        selectedTab = .home
        if let api, !history.contains(where: { $0.id == callID }) {
            await loadHistory(api)
        }
        presentedCall = nil
        if let summary = history.first(where: { $0.id == callID }) {
            path = [summary]
        }
    }

    func startDemoCall(_ script: DemoScript) async {
        guard let api else { return }
        do {
            try await api.startDemoCall(scriptID: script.id)
            demoError = nil
        } catch {
            demoError = Self.describe(error)
        }
    }

    // MARK: - Feed

    private func follow(_ api: ShieldAPI) async {
        var retryDelay = 1.0
        while !Task.isCancelled {
            await loadServerInfo(api)
            do {
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
                connection = .failed(Self.describe(error))
            }
            try? await Task.sleep(for: .seconds(retryDelay))
            retryDelay = min(retryDelay * 2, 8)
        }
    }

    private func handle(_ event: ShieldEvent, isFresh: Bool) {
        if event.type == "gateway_message_updated" {
            if let id = event.messageID, event.status == "held", isFresh,
               !seenGatewayIDs.contains(id), alertedGatewayIDs.insert(id).inserted {
                gatewayBannerID = id
            }
            if let api { Task { await self.loadGateway(api) } }
            return
        }
        if event.type == "message_scored" {
            if isFresh, let api {
                Task { await self.loadMessages(api) }
            }
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
        if event.type == "call_ended", isFresh, let api {
            Task { await self.loadHistory(api) }
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
            guard self.api == api else { return }
            gatewayHealth = health.gateway
            warnThreshold = health.warnThreshold ?? warnThreshold
            endThreshold = health.endThreshold ?? endThreshold
        }
        demoScripts = (try? await api.demoScripts()) ?? []
        await loadHistory(api)
        await loadMessages(api)
        if gatewayHealth?.enabled == true { await loadGateway(api) }
    }

    private func loadGateway(_ api: ShieldAPI) async {
        do {
            let list = try await api.gatewayMessages()
            guard self.api == api else { return }
            gatewayMessages = list.messages
            shieldNumber = list.shieldNumber
            gatewayError = nil
            // A pending record has not raised a review alert yet. Remember only
            // held records so delayed analysis can still trigger its first banner.
            alertedGatewayIDs.formUnion(list.messages.filter(\.needsReview).map(\.id))
            unreadGatewayIDs = Set(list.messages.filter { $0.needsReview && !seenGatewayIDs.contains($0.id) }.map(\.id))
            if selectedTab == .texts { markGatewayRead() }
        } catch {
            if self.api == api { gatewayError = Self.describe(error) }
        }
    }

    private func loadMessages(_ api: ShieldAPI) async {
        if let messages = try? await api.messages(), self.api == api {
            self.messages = messages
        }
    }

    private func loadHistory(_ api: ShieldAPI) async {
        if let calls = try? await api.calls() {
            history = calls
        }
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
                return "Can’t reach the Shield server."
            default:
                break
            }
        }
        return "Something went wrong talking to the Shield server."
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
        demoScripts: [DemoScript] = []
    ) -> ShieldStore {
        let store = ShieldStore()
        store.followsSettings = false
        store.connection = connection
        store.call = call
        store.presentedCall = isShowingCall ? call : nil
        store.history = history
        store.demoScripts = demoScripts
        return store
    }
}
#endif
