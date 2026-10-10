import Foundation

/// One event from the Shield server's `/events` feed.
///
/// The server sends a flat JSON object whose fields depend on `type`, so everything
/// except `type` and `ts` is optional.
struct ShieldEvent: Decodable, Equatable, Sendable {
    let type: String
    let ts: Double
    var callSID: String?
    var caller: String?
    var simulated: Bool?
    var speaker: String?
    var text: String?
    /// On a caller's line: the phrases in `text` that matched one of Shield's rules.
    var highlights: [String]?
    var isFinal: Bool?
    var t: Double?
    var risk: Int?
    var ruleRisk: Int?
    var flags: [String]?
    var kind: String?
    var reason: String?
    var seconds: Double?
    var outcome: String?
    var duration: Double?

    enum CodingKeys: String, CodingKey {
        case type, ts, caller, simulated, speaker, text, highlights, t, risk, flags, kind, reason, seconds, outcome
        case callSID = "call_sid"
        case isFinal = "final"
        case ruleRisk = "rule_risk"
        case duration = "duration_s"
    }

    var date: Date { Date(timeIntervalSince1970: ts) }
}

/// What Shield did about a call: a warning to the caregiver, or ending the call.
struct ShieldAction: Equatable, Sendable {
    enum Kind: String, Sendable {
        case warn = "WARN"
        case end = "END"
    }

    let kind: Kind
    let risk: Int
    let reason: String
    let at: Date
    /// Seconds from the caller's triggering words to the action. Arrives a moment after the action itself.
    var seconds: Double?
}
