import Foundation

// Standalone state-store regression tests. Compile with the real ShieldStore
// and models, replacing only its HTTP transport with this in-memory fixture.
// Run: bash tools/test_ios_gateway.sh
enum ShieldAPIError: LocalizedError {
    case unauthorized
    case refused(String)
    case notAShieldServer
}

// The store asks the server whether it wants a sign-in. This stand-in server wants none, so
// the real `SignIn`, which needs Auth0's library, is never made.
struct SignInConfig: Equatable, Sendable {
    static let off = SignInConfig()
}

final class SignIn: Sendable {
    let config: SignInConfig
    init?(_ config: SignInConfig) { return nil }
    var account: String? { nil }
    func signIn() async throws {}
    func signOut() async {}
    func accessToken() async throws -> String? { nil }
}

@MainActor
final class Fixture {
    static let shared = Fixture()
    var messages: [GatewayMessage] = []
    /// The calls the stand-in server lists. Empty until a check about calls fills it.
    var calls: [CallSummary] = []
    /// Whether the stand-in server has a privacy policy and a support page to show.
    var hasPages = false
    var continuation: AsyncThrowingStream<ShieldAPI.FeedItem, Error>.Continuation?
}

struct ShieldAPI: Equatable, Sendable {
    enum FeedItem: Sendable { case connected; case event(ShieldEvent) }
    let baseURL = URL(string: "http://shield-store-test.invalid")!
    @MainActor static var fromSettings: ShieldAPI? { ShieldAPI() }
    func with(token: String) -> ShieldAPI { self }
    @MainActor func signInConfig() async throws -> SignInConfig { .off }
    @MainActor func hasPublicPages() async -> Bool { Fixture.shared.hasPages }
    @MainActor func health() async throws -> ServerHealth { ServerHealth(ok: true, gateway: GatewayHealth(enabled: true, forwardingEnabled: false, connected: false, smsReady: false)) }
    @MainActor func calls() async throws -> [CallSummary] { Fixture.shared.calls }
    @MainActor func markCall(_ id: String, wrong: Bool) async throws -> CallSummary {
        let index = Fixture.shared.calls.firstIndex { $0.id == id }!
        Fixture.shared.calls[index].markedWrong = wrong
        return Fixture.shared.calls[index]
    }
    @MainActor func messages() async throws -> [MessageSummary] { [] }
    @MainActor func gatewayMessages() async throws -> GatewayMessageList { GatewayMessageList(messages: Fixture.shared.messages, shieldNumber: "+15555550900", forwardingEnabled: false) }
    @MainActor func gatewayDetail(_ id: String) async throws -> GatewayMessage { Fixture.shared.messages.first { $0.id == id }! }
    @MainActor func gatewayDecision(_ id: String, action: String) async throws -> GatewayMessage { try await gatewayDetail(id) }
    @MainActor func analyzeMessage(_ text: String) async throws -> MessageSummary { throw ShieldAPIError.notAShieldServer }
    @MainActor func demoScripts() async throws -> [DemoScript] { [] }
    @MainActor func startDemoCall(scriptID: String) async throws {}
    @MainActor func events() -> AsyncThrowingStream<FeedItem, Error> {
        AsyncThrowingStream { continuation in
            Fixture.shared.continuation = continuation
            continuation.yield(.connected)
        }
    }
}

@main
struct StoreGatewayTests {
    @MainActor static func message(_ id: String, status: String) -> GatewayMessage {
        GatewayMessage(messageID: id, ts: Date.now.timeIntervalSince1970, sender: "***0999", status: status,
                       risk: status == "pending" ? nil : 81, label: status == "pending" ? nil : "likely_scam",
                       flags: [], holdReason: status == "held" ? "scam_signals" : nil,
                       expiresAt: Date.now.addingTimeInterval(86400).timeIntervalSince1970,
                       reviewEligible: status == "held", explanation: "Test", text: nil, originalSender: nil)
    }

    /// A call Palisade ended, as the server lists it. `markedWrong` nil is a server from before marks.
    @MainActor static func endedCall(_ id: String, markedWrong: Bool?) -> CallSummary {
        CallSummary(id: id, startedAtSeconds: Date.now.timeIntervalSince1970 - 600, caller: "***0999", isDemo: false,
                    outcome: .endedByShield, duration: 30, peakRisk: 92, flagIDs: [], actions: [], timeline: [],
                    markedWrong: markedWrong)
    }

    static func decoded(_ json: String) -> CallSummary {
        try! JSONDecoder().decode(CallSummary.self, from: Data(json.utf8))
    }

    @MainActor static func pause() async { try? await Task.sleep(for: .milliseconds(100)) }

    @MainActor static func main() async {
        let id = "pending-then-held-" + UUID().uuidString
        Fixture.shared.messages = [message(id, status: "pending")]
        let store = ShieldStore()
        store.connect()
        await pause()
        assert(store.connection == .connected, "Fixture event feed must be connected")
        assert(store.gatewayMessages.first?.status == "pending")
        Fixture.shared.messages = [message(id, status: "held")]
        Fixture.shared.continuation?.yield(.event(ShieldEvent(type: "gateway_message_updated", ts: Date.now.timeIntervalSince1970, messageID: id, status: "held")))
        await pause()
        guard store.latestHeldText == id else {
            print("FAIL: pending text becoming held did not raise the alert for a text just held")
            exit(1)
        }
        print("PASS: pending text becoming held raises the alert for a text just held")

        store.latestHeldText = nil
        Fixture.shared.continuation?.yield(.event(ShieldEvent(type: "gateway_message_updated", ts: Date.now.timeIntervalSince1970, messageID: id, status: "held")))
        await pause()
        assert(store.latestHeldText == nil, "A replayed held event must not raise another alert")
        assert(store.unreadGatewayIDs.contains(id), "Unread review must retain its Texts badge")
        print("PASS: repeated events suppress duplicate alerts and retain the unread badge")

        store.markGatewayRead()
        assert(store.unreadGatewayIDs.isEmpty)
        store.connect()
        await pause()
        Fixture.shared.continuation?.yield(.event(ShieldEvent(type: "gateway_message_updated", ts: Date.now.timeIntervalSince1970, messageID: id, status: "held")))
        await pause()
        assert(store.latestHeldText == nil, "A previously read ID must remain suppressed after reconnect")
        assert(store.unreadGatewayIDs.isEmpty)
        print("PASS: read messages stay read after reconnect and replay")

        let callID = "active-call-" + UUID().uuidString
        Fixture.shared.continuation?.yield(.event(ShieldEvent(type: "call_started", ts: Date.now.timeIntervalSince1970, callSID: callID, caller: "***0999", simulated: true)))
        await pause()
        assert(store.presentedCall?.id == callID)
        let nextID = "held-during-call-" + UUID().uuidString
        Fixture.shared.messages.append(message(nextID, status: "held"))
        Fixture.shared.continuation?.yield(.event(ShieldEvent(type: "gateway_message_updated", ts: Date.now.timeIntervalSince1970, messageID: nextID, status: "held")))
        await pause()
        assert(store.presentedCall?.id == callID, "A text alert must not replace the call overlay")
        assert(store.latestHeldText == nextID)
        assert(store.unreadGatewayIDs.contains(nextID))
        print("PASS: held texts preserve the active call and update review state")

        store.latestHeldText = nil
        let oldID = "old-event-" + UUID().uuidString
        Fixture.shared.messages.append(message(oldID, status: "held"))
        Fixture.shared.continuation?.yield(.event(ShieldEvent(type: "gateway_message_updated", ts: Date.now.addingTimeInterval(-60).timeIntervalSince1970, messageID: oldID, status: "held")))
        await pause()
        assert(store.latestHeldText == nil, "Old replay events must not raise an alert")
        assert(store.unreadGatewayIDs.contains(oldID), "Old held texts should still be available for review")
        print("PASS: historical events refresh review state without an alert")

        assert(store.publicPages == nil, "A server without the public pages must leave Settings with no link to them")
        Fixture.shared.hasPages = true
        store.connect()
        await pause()
        assert(store.publicPages == ShieldAPI().baseURL, "A server with the public pages must give Settings their address")
        Fixture.shared.hasPages = false
        store.connect()
        await pause()
        guard store.publicPages == nil else {
            print("FAIL: links to the server's pages stayed after the server stopped having them")
            exit(1)
        }
        print("PASS: links to the server's pages are offered only while it has them")

        let markedID = "marked-call-" + UUID().uuidString
        Fixture.shared.calls = [endedCall(markedID, markedWrong: false)]
        store.connect()
        await pause()
        assert(store.history.first?.id == markedID && store.stats.blocked == 1, "The ended call must be listed and counted")
        try? await store.markCall(markedID, wrong: true)
        guard store.history.first?.markedWrong == true, store.stats.blocked == 0 else {
            print("FAIL: marking a call wrong did not change it in the list, or it still counts as a scam stopped")
            exit(1)
        }
        print("PASS: a call marked wrong changes in the list and no longer counts as a scam stopped")

        // The mark taken back on another phone: this one only hears that a call was marked.
        Fixture.shared.calls = [endedCall(markedID, markedWrong: false)]
        Fixture.shared.continuation?.yield(.event(ShieldEvent(type: "call_marked", ts: Date.now.timeIntervalSince1970, callSID: markedID)))
        await pause()
        guard store.history.first?.markedWrong == false, store.stats.blocked == 1 else {
            print("FAIL: a mark changed on another phone did not reach this one")
            exit(1)
        }
        print("PASS: a mark changed on another phone reaches this one")

        let call = #""call_sid":"CA1","started_at":1,"caller":"***0001","simulated":false,"duration_s":3,"peak_risk":0,"flags":[],"actions":[],"timeline":[]"#
        let fromBeforeMarks = decoded("{\(call),\"outcome\":\"completed\"}")
        let leftAlone = decoded("{\(call),\"outcome\":\"completed\",\"marked_wrong\":false}")
        let warned = decoded("{\(call),\"outcome\":\"warned\",\"marked_wrong\":true}")
        let stillGoing = decoded("{\(call),\"outcome\":\"in_progress\",\"marked_wrong\":false}")
        guard fromBeforeMarks.markedWrong == nil, !fromBeforeMarks.canBeMarked,
              leftAlone.canBeMarked, !leftAlone.wasSteppedInto,
              warned.canBeMarked, warned.wasSteppedInto, warned.markedWrong == true,
              !stillGoing.canBeMarked
        else {
            print("FAIL: a call can be marked only when it is over and the server keeps marks")
            exit(1)
        }
        print("PASS: a call can be marked only when it is over and the server keeps marks")

        UserDefaults.standard.removeObject(forKey: "gatewaySeen.http://shield-store-test.invalid")
        print("9 Swift gateway state-store checks passed")
    }
}
