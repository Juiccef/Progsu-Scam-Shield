import Foundation

struct MessageSummary: Decodable, Identifiable, Sendable {
    let messageID: String
    let ts: Double
    let sender: String
    let source: String
    let risk: Int
    let label: String
    let flags: [String]
    let explanation: String?
    let textOnly: Bool

    var id: String { messageID }
    var date: Date { Date(timeIntervalSince1970: ts) }
    var title: String {
        switch label {
        case "likely_scam": "Likely scam"
        case "suspicious": "Suspicious"
        default: "No strong scam signals"
        }
    }

    enum CodingKeys: String, CodingKey {
        case ts, sender, source, risk, label, flags, explanation
        case messageID = "message_id"
        case textOnly = "text_only"
    }
}

struct MessageList: Decodable {
    let messages: [MessageSummary]
}
