import Foundation

/// A text sent to the protected number, as the server summarizes it in `GET /gateway/messages`:
/// who sent it, what Palisade made of it, and where it is now. Never what it says. The words
/// and the sender's whole number come only from `GET /gateway/messages/{id}`, and only while
/// the server still keeps them.
struct GatewayMessage: Decodable, Identifiable, Sendable {
    /// Where a text is on its way through Palisade.
    enum State: String, Sendable {
        case pending, held, queued, sending, sent, delivered, discarded, expired
        case deliveryFailed = "delivery_failed"
        case deliveryUnknown = "delivery_unknown"
        /// A status this version of the app has not heard of.
        case unknown
    }

    /// Why Palisade kept a text back.
    enum HoldReason: String, Sendable {
        case scamSigns = "scam_signals"
        case notChecked = "analysis_incomplete"
        case attachment = "unsupported_content"
        case tooLong = "message_too_long"
        /// Left from an earlier way of delivering texts. It can only be discarded.
        case oldRecord = "transport_changed"
    }

    let messageID: String
    let ts: Double
    /// Masked to its last four digits, like a caller's number.
    let sender: String
    let status: String
    let risk: Int?
    let label: String?
    let flags: [String]
    let holdReason: String?
    let expiresAt: Double
    let reviewEligible: Bool
    /// The server's advice, one of a few fixed sentences. Never anything the sender wrote.
    let explanation: String
    // These two come only with the request for one text.
    let text: String?
    let originalSender: String?

    var id: String { messageID }
    var date: Date { Date(timeIntervalSince1970: ts) }
    var expiry: Date { Date(timeIntervalSince1970: expiresAt) }
    var state: State { State(rawValue: status) ?? .unknown }
    var hold: HoldReason? { holdReason.flatMap(HoldReason.init(rawValue:)) }
    var needsReview: Bool { status == "held" }
    /// True for a text Palisade kept back, whatever was decided about it afterwards.
    var wasHeld: Bool { holdReason != nil }
    var verdict: TextVerdict { TextVerdict(label: label) }
    /// Most telling first, as on a call.
    var signs: [Flag] { flags.map(Flag.init(id:)).sorted { $0.rank < $1.rank } }

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

/// What `GET /health` says about text protection.
struct GatewayHealth: Decodable, Equatable, Sendable {
    let enabled: Bool
    /// False while the server checks and keeps texts but does not send them on.
    let forwardingEnabled: Bool?
    /// True while the service that sends the caregiver's iMessage alerts is checking in.
    let connected: Bool?
    /// False when the server could not send a text by SMS if asked: practice mode, or no Twilio account.
    let smsReady: Bool?
    /// How an approved text reaches the protected person: "sms", or "imessage" through the same
    /// agent that alerts the caregiver. A server from before iMessage says nothing, and means SMS.
    var forwardVia: String?
    /// Whether an approved text could be sent on right now, whichever way the server sends them.
    var forwardingReady: Bool?
    /// Why text protection is off, when the server was asked for it and could not start it.
    var error: String?

    /// True when texts go to the protected person by iMessage rather than SMS.
    var forwardsByIMessage: Bool { forwardVia == "imessage" }

    enum CodingKeys: String, CodingKey {
        case enabled, connected, error
        case forwardingEnabled = "forwarding_enabled"
        case smsReady = "sms_ready"
        case forwardVia = "forward_via"
        case forwardingReady = "forwarding_ready"
    }
}
