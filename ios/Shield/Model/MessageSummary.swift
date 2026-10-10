import Foundation

/// What Palisade made of a text, from the server's `label`.
enum TextVerdict: Sendable {
    case likelyScam
    case suspicious
    /// Nothing that looks like a scam. Not a promise that the sender is who they say.
    case ordinary

    init(label: String?) {
        switch label {
        case "likely_scam": self = .likelyScam
        case "suspicious": self = .suspicious
        default: self = .ordinary
        }
    }
}

/// The result of checking one text: one the caregiver pasted into the app or, on a server with
/// text protection switched off, one sent to its number. Never the text itself.
struct MessageSummary: Decodable, Identifiable, Sendable {
    let messageID: String
    let ts: Double
    let sender: String
    /// "manual" for a text pasted into the app.
    let source: String
    let risk: Int
    let label: String
    let flags: [String]
    /// The server's advice. It is not kept on the server's disk, so an older result has none.
    let explanation: String?
    let textOnly: Bool

    var id: String { messageID }
    var date: Date { Date(timeIntervalSince1970: ts) }
    var isPasted: Bool { source == "manual" }
    var verdict: TextVerdict { TextVerdict(label: label) }
    var signs: [Flag] { flags.map(Flag.init(id:)).sorted { $0.rank < $1.rank } }

    enum CodingKeys: String, CodingKey {
        case ts, sender, source, risk, label, flags, explanation
        case messageID = "message_id"
        case textOnly = "text_only"
    }
}

struct MessageList: Decodable {
    let messages: [MessageSummary]
}
