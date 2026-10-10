import Foundation

/// Keys for the values the caregiver sets once in Settings. A launch argument such as
/// `-serverURL http://localhost:8000` overrides the stored value, which is handy in the simulator.
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
    case server(Int)
    case notAShieldServer
    case reviewConflict
    case gatewayUnavailable

    var errorDescription: String? {
        switch self {
        case .unauthorized: "The access token was not accepted."
        case .server(let status): "The server answered with error \(status)."
        case .notAShieldServer: "That address did not answer like a Shield server."
        case .reviewConflict: "This message can no longer be released. Refresh to see its current status."
        case .gatewayUnavailable: "Text protection is not enabled on this server."
        }
    }
}

/// The Shield server's HTTP interface.
struct ShieldAPI: Equatable, Sendable {
    enum FeedItem: Sendable {
        case connected
        case event(ShieldEvent)
    }

    let baseURL: URL
    let token: String

    /// Builds a client from what the caregiver typed. Accepts "shield.ngrok-free.app" or a full URL.
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
        let defaults = UserDefaults.standard
        return ShieldAPI(
            address: defaults.string(forKey: SettingsKey.serverURL) ?? SettingsKey.defaultServerURL,
            token: defaults.string(forKey: SettingsKey.accessToken) ?? ""
        )
    }

    func health() async throws -> ServerHealth {
        let health: ServerHealth = try await get("health")
        guard health.ok else { throw ShieldAPIError.notAShieldServer }
        return health
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
        var request = request("gateway/messages/\(id)/decision")
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONEncoder().encode(["action": action])
        let (data, response) = try await URLSession.shared.data(for: request)
        try Self.check(response)
        return try JSONDecoder().decode(GatewayMessage.self, from: data)
    }

    func analyzeMessage(_ text: String) async throws -> MessageSummary {
        var request = request("messages/analyze")
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONEncoder().encode(["text": text])
        let (data, response) = try await URLSession.shared.data(for: request)
        try Self.check(response)
        return try JSONDecoder().decode(MessageSummary.self, from: data)
    }

    func demoScripts() async throws -> [DemoScript] {
        let list: DemoScriptList = try await get("demo/scripts")
        return list.enabled ? list.scripts : []
    }

    func startDemoCall(scriptID: String) async throws {
        var request = request("demo/call", query: [URLQueryItem(name: "script", value: scriptID)])
        request.httpMethod = "POST"
        let (_, response) = try await URLSession.shared.data(for: request)
        try Self.check(response)
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
        let (data, response) = try await URLSession.shared.data(for: request(path))
        try Self.check(response)
        do {
            return try JSONDecoder().decode(Value.self, from: data)
        } catch {
            throw ShieldAPIError.notAShieldServer
        }
    }

    private func request(_ path: String, query: [URLQueryItem] = []) -> URLRequest {
        var url = baseURL.appending(path: path)
        if !query.isEmpty { url.append(queryItems: query) }
        var request = URLRequest(url: url)
        if path.hasPrefix("gateway/") {
            request.cachePolicy = .reloadIgnoringLocalAndRemoteCacheData
            request.setValue("no-store", forHTTPHeaderField: "Cache-Control")
        }
        request.timeoutInterval = 10
        if !token.isEmpty { request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization") }
        // Without this, ngrok's free tier answers the first request with an HTML warning page.
        request.setValue("1", forHTTPHeaderField: "ngrok-skip-browser-warning")
        return request
    }

    private static func check(_ response: URLResponse) throws {
        guard let status = (response as? HTTPURLResponse)?.statusCode else { return }
        if status == 401 { throw ShieldAPIError.unauthorized }
        if status == 409 { throw ShieldAPIError.reviewConflict }
        if status == 503 { throw ShieldAPIError.gatewayUnavailable }
        guard (200..<300).contains(status) else { throw ShieldAPIError.server(status) }
    }
}
