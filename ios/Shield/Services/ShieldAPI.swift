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

    var errorDescription: String? {
        switch self {
        case .unauthorized: "The access token was not accepted."
        case .server(let status): "The server answered with error \(status)."
        case .notAShieldServer: "That address did not answer like a Shield server."
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
        request.timeoutInterval = 10
        if !token.isEmpty { request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization") }
        // Without this, ngrok's free tier answers the first request with an HTML warning page.
        request.setValue("1", forHTTPHeaderField: "ngrok-skip-browser-warning")
        return request
    }

    private static func check(_ response: URLResponse) throws {
        guard let status = (response as? HTTPURLResponse)?.statusCode else { return }
        if status == 401 { throw ShieldAPIError.unauthorized }
        guard (200..<300).contains(status) else { throw ShieldAPIError.server(status) }
    }
}
