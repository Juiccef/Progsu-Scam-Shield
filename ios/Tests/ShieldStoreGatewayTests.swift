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
    var continuation: AsyncThrowingStream<ShieldAPI.FeedItem, Error>.Continuation?
}

struct ShieldAPI: Equatable, Sendable {
    enum FeedItem: Sendable { case connected; case event(ShieldEvent) }
    let baseURL = URL(string: "http://shield-store-test.invalid")!
    @MainActor static var fromSettings: ShieldAPI? { ShieldAPI() }
    func with(token: String) -> ShieldAPI { self }
    @MainActor func signInConfig() async throws -> SignInConfig { .off }
    @MainActor func health() async throws -> ServerHealth { ServerHealth(ok: true, gateway: GatewayHealth(enabled: true, forwardingEnabled: false, connected: false, smsReady: false)) }
    @MainActor func calls() async throws -> [CallSummary] { [] }
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
        guard store.gatewayBannerID == id else {
            print("FAIL: pending text becoming held did not show the fresh-message banner")
            exit(1)
        }
        print("PASS: pending text becoming held shows the fresh-message banner")

        store.gatewayBannerID = nil
        Fixture.shared.continuation?.yield(.event(ShieldEvent(type: "gateway_message_updated", ts: Date.now.timeIntervalSince1970, messageID: id, status: "held")))
        await pause()
        assert(store.gatewayBannerID == nil, "A replayed held event must not raise another banner")
        assert(store.unreadGatewayIDs.contains(id), "Unread review must retain its Texts badge")
        print("PASS: repeated events suppress duplicate banners and retain the unread badge")

        store.markGatewayRead()
        assert(store.unreadGatewayIDs.isEmpty)
        store.connect()
        await pause()
        Fixture.shared.continuation?.yield(.event(ShieldEvent(type: "gateway_message_updated", ts: Date.now.timeIntervalSince1970, messageID: id, status: "held")))
        await pause()
        assert(store.gatewayBannerID == nil, "A previously read ID must remain suppressed after reconnect")
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
        assert(store.gatewayBannerID == nextID)
        assert(store.unreadGatewayIDs.contains(nextID))
        print("PASS: held texts preserve the active call and update review state")

        store.gatewayBannerID = nil
        let oldID = "old-event-" + UUID().uuidString
        Fixture.shared.messages.append(message(oldID, status: "held"))
        Fixture.shared.continuation?.yield(.event(ShieldEvent(type: "gateway_message_updated", ts: Date.now.addingTimeInterval(-60).timeIntervalSince1970, messageID: oldID, status: "held")))
        await pause()
        assert(store.gatewayBannerID == nil, "Old replay events must not show a fresh banner")
        assert(store.unreadGatewayIDs.contains(oldID), "Old held texts should still be available for review")
        print("PASS: historical events refresh review state without a fresh banner")

        UserDefaults.standard.removeObject(forKey: "gatewaySeen.http://shield-store-test.invalid")
        print("5 Swift gateway state-store checks passed")
    }
}
