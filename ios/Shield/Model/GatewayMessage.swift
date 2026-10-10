import Foundation

struct GatewayMessage: Decodable, Identifiable, Sendable {
    let messageID: String
    let ts: Double
    let sender: String
    let status: String
    let risk: Int?
    let label: String?
    let flags: [String]
    let holdReason: String?
    let expiresAt: Double
    let reviewEligible: Bool
    let explanation: String
    // These private fields appear only on the authenticated detail endpoint.
    let text: String?
    let originalSender: String?

    var id: String { messageID }
    var date: Date { Date(timeIntervalSince1970: ts) }
    var expiry: Date { Date(timeIntervalSince1970: expiresAt) }
    var needsReview: Bool { status == "held" }
    var title: String {
        switch holdReason {
        case "unsupported_content": return "Unsupported content"
        case "analysis_incomplete": return "Analysis incomplete"
        case "message_too_long": return "Text exceeds SMS limit"
        case "transport_changed": return "Previous transport record"
        default:
            if label == "likely_scam" { return "Likely scam" }
            if label == "suspicious" { return "Suspicious text" }
            return risk == nil ? "Awaiting analysis" : "Passed screening"
        }
    }
    var statusTitle: String {
        switch status {
        case "pending": "Checking"
        case "held": "Needs review"
        case "queued": "Queued"
        case "sending": "Sending"
        case "sent": "Sent by Shield"
        case "delivered": "Delivered"
        case "delivery_failed": "Delivery failed"
        case "discarded": "Discarded"
        case "expired": "Expired"
        case "delivery_unknown": "Delivery unconfirmed"
        default: "Unavailable"
        }
    }

    enum CodingKeys: String, CodingKey {
        case ts, sender, status, risk, label, flags, explanation, text
        case messageID = "message_id"
        case holdReason = "hold_reason"
        case expiresAt = "expires_at"
        case reviewEligible = "review_eligible"
        case originalSender = "original_sender"
    }
}

struct GatewayMessageList: Decodable {
    let messages: [GatewayMessage]
    let shieldNumber: String
    let forwardingEnabled: Bool
    enum CodingKeys: String, CodingKey {
        case messages
        case shieldNumber = "shield_number"
        case forwardingEnabled = "forwarding_enabled"
    }
}

struct GatewayHealth: Decodable, Equatable, Sendable {
    let enabled: Bool
    let forwardingEnabled: Bool?
    let connected: Bool?
    let smsReady: Bool?
    enum CodingKeys: String, CodingKey {
        case enabled, connected
        case forwardingEnabled = "forwarding_enabled"
        case smsReady = "sms_ready"
    }
}
