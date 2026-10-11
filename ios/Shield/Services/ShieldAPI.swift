import Foundation

/// Keys for the values the caregiver sets once in Settings. A launch argument such as
/// `-serverURL http://localhost:8000` overrides the stored value, which is handy in the simulator.
/// The Access Token is the exception: it is kept in the Keychain (see `AccessToken`).
enum SettingsKey {
    static let serverURL = "serverURL"
    static let accessToken = "accessToken"
    static let seniorName = "seniorName"
    static let seniorPhone = "seniorPhone"
    static let appearance = "appearance"

    static let defaultServerURL = "http://localhost:8000"
    static let defaultSeniorName = "Mom"
}

enum ShieldAPIError: LocalizedError {
    case unauthorized
    /// The server knows who is signed in and does not let them watch calls. Its own sentence says why.
    case refused(String)
    case server(Int)
    case notAShieldServer
    /// A held text was released, discarded or ran out before this decision reached the server.
    case reviewConflict
    /// The server has text protection switched off.
    case gatewayUnavailable

    var errorDescription: String? {
        switch self {
        case .unauthorized: "The access token was not accepted."
        case .refused(let reason): reason
        case .server(let status): "The server answered with error \(status)."
        case .notAShieldServer: "That address did not answer like a Palisade server."
        case .reviewConflict: "This text is no longer waiting for a decision."
        case .gatewayUnavailable: "Text protection is switched off on this server."
        }
    }
}

/// The Palisade server's HTTP interface.
struct ShieldAPI: Equatable, Sendable {
    enum FeedItem: Sendable {
        case connected
        case event(ShieldEvent)
    }

    let baseURL: URL
    private(set) var token: String

    /// Builds a client from what the caregiver typed. Accepts "palisade.ngrok-free.app" or a full URL.
    init?(address: String, token: String) {
        var text = address.trimmingCharacters(in: .whitespacesAndNewlines)
        while text.hasSuffix("/") { text.removeLast() }
        guard !text.isEmpty else { return nil }
        if !text.contains("://") { text = "https://" + text }
        guard let url = URL(string: text), url.host() != nil else { return nil }
        baseURL = url
        self.token = token.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    static var fromSettings: ShieldAPI? {
        ShieldAPI(
            address: UserDefaults.standard.string(forKey: SettingsKey.serverURL) ?? SettingsKey.defaultServerURL,
            token: AccessToken.current
        )
    }

    /// The same server, asked with another token: the one a signed-in caregiver gets from Auth0.
    func with(token: String) -> ShieldAPI {
        var copy = self
        copy.token = token
        return copy
    }

    func health() async throws -> ServerHealth {
        let health: ServerHealth = try await get("health")
        guard health.ok else { throw ShieldAPIError.notAShieldServer }
        return health
    }

    /// Whether this server wants a sign-in, and with which Auth0 tenant. Throws only when the
    /// server is out of reach: one from before sign-in has no such page, and asks for none.
    func signInConfig() async throws -> SignInConfig {
        do {
            return try await get("auth/config")
        } catch is ShieldAPIError {
            return .off
        }
    }

    func calls() async throws -> [CallSummary] {
        let list: CallList = try await get("calls")
        return list.calls
    }

    func messages() async throws -> [MessageSummary] {
        let list: MessageList = try await get("messages")
        return list.messages
    }

    func gatewayMessages() async throws -> GatewayMessageList {
        try await get("gateway/messages")
    }

    func gatewayDetail(_ id: String) async throws -> GatewayMessage {
        try await get("gateway/messages/\(id)")
    }

    func gatewayDecision(_ id: String, action: String) async throws -> GatewayMessage {
        try await post("gateway/messages/\(id)/decision", json: ["action": action])
    }

    func analyzeMessage(_ text: String) async throws -> MessageSummary {
        try await post("messages/analyze", json: ["text": text])
    }

    func demoScripts() async throws -> [DemoScript] {
        let list: DemoScriptList = try await get("demo/scripts")
        return list.enabled ? list.scripts : []
    }

    func startDemoCall(scriptID: String) async throws {
        var request = request("demo/call", query: [URLQueryItem(name: "script", value: scriptID)])
        request.httpMethod = "POST"
        let (data, response) = try await URLSession.shared.data(for: request)
        try Self.check(response, body: data)
    }

    /// The live event feed. Starts with recent events, so a call already in progress can be rebuilt.
    func events() -> AsyncThrowingStream<FeedItem, Error> {
        var request = request("events", query: [URLQueryItem(name: "replay", value: "true")])
        request.setValue("text/event-stream", forHTTPHeaderField: "Accept")
        request.timeoutInterval = 3600  // the server sends a keep-alive every 15 seconds
        return AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    let (bytes, response) = try await URLSession.shared.bytes(for: request)
                    if (response as? HTTPURLResponse)?.statusCode == 403 {
                        // A refusal carries the server's reason, in a line of JSON.
                        var body = Data()
                        for try await byte in bytes.prefix(2048) { body.append(byte) }
                        try Self.check(response, body: body)
                    }
                    try Self.check(response)
                    continuation.yield(.connected)
                    let decoder = JSONDecoder()
                    for try await line in bytes.lines where line.hasPrefix("data:") {
                        if let event = try? decoder.decode(ShieldEvent.self, from: Data(line.dropFirst(5).utf8)) {
                            continuation.yield(.event(event))
                        }
                    }
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }

    private func get<Value: Decodable>(_ path: String) async throws -> Value {
        try await answer(to: request(path), isText: Self.isText(path))
    }

    private func post<Value: Decodable>(_ path: String, json: [String: String]) async throws -> Value {
        var request = request(path)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONEncoder().encode(json)
        return try await answer(to: request, isText: Self.isText(path))
    }

    private func answer<Value: Decodable>(to request: URLRequest, isText: Bool) async throws -> Value {
        let (data, response) = try await URLSession.shared.data(for: request)
        try Self.check(response, body: data, isText: isText)
        do {
            return try JSONDecoder().decode(Value.self, from: data)
        } catch {
            throw ShieldAPIError.notAShieldServer
        }
    }

    /// The requests about texts held at the protected number, whose answers can carry a text
    /// and its sender in full.
    private static func isText(_ path: String) -> Bool {
        path.hasPrefix("gateway/")
    }

    private func request(_ path: String, query: [URLQueryItem] = []) -> URLRequest {
        var url = baseURL.appending(path: path)
        if !query.isEmpty { url.append(queryItems: query) }
        var request = URLRequest(url: url)
        if Self.isText(path) {
            request.cachePolicy = .reloadIgnoringLocalAndRemoteCacheData
            request.setValue("no-store", forHTTPHeaderField: "Cache-Control")
        }
        request.timeoutInterval = 10
        if !token.isEmpty { request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization") }
        // Without this, ngrok's free tier answers the first request with an HTML warning page.
        request.setValue("1", forHTTPHeaderField: "ngrok-skip-browser-warning")
        return request
    }

    private static func check(_ response: URLResponse, body: Data? = nil, isText: Bool = false) throws {
        guard let status = (response as? HTTPURLResponse)?.statusCode else { return }
        if status == 401 { throw ShieldAPIError.unauthorized }
        if status == 403, let body, let refusal = try? JSONDecoder().decode(Refusal.self, from: body) {
            throw ShieldAPIError.refused(refusal.detail.hasSuffix(".") ? refusal.detail : refusal.detail + ".")
        }
        // Only on a request about a held text do these two mean what they say here. A demo
        // call the server turns down is a 409 as well, and a server that is down a 503.
        if isText, status == 409 { throw ShieldAPIError.reviewConflict }
        if isText, status == 503 { throw ShieldAPIError.gatewayUnavailable }
        guard (200..<300).contains(status) else { throw ShieldAPIError.server(status) }
    }

    /// How the server words a refusal: `{"detail": "This account is not on the caregiver list"}`.
    private struct Refusal: Decodable {
        let detail: String
    }
}
