import Foundation

/// A finished (or running) call as the server summarizes it in `GET /calls`.
/// It records what Palisade detected and did, never what was said.
struct CallSummary: Decodable, Identifiable, Hashable, Sendable {
    enum Outcome: String, Decodable, Sendable {
        case inProgress = "in_progress"
        case completed
        case warned
        case endedByShield = "ended_by_shield"
    }

    struct Action: Decodable, Hashable, Sendable {
        let kind: String
        let risk: Int?
        let reason: String
        let at: Double?
        let seconds: Double?
    }

    let id: String
    let startedAtSeconds: Double?
    let caller: String
    let isDemo: Bool
    let outcome: Outcome
    let duration: Double?
    let peakRisk: Int
    let flagIDs: [String]
    let actions: [Action]
    /// `[seconds into the call, risk]` pairs.
    let timeline: [[Double]]
    /// The caregiver's word that Palisade got this call wrong. Nil from a server that keeps no
    /// such mark, which is how the app knows not to offer one.
    var markedWrong: Bool? = nil

    enum CodingKeys: String, CodingKey {
        case id = "call_sid"
        case startedAtSeconds = "started_at"
        case caller
        case isDemo = "simulated"
        case outcome
        case duration = "duration_s"
        case peakRisk = "peak_risk"
        case flagIDs = "flags"
        case actions, timeline
        case markedWrong = "marked_wrong"
    }

    var startedAt: Date? { startedAtSeconds.map(Date.init(timeIntervalSince1970:)) }
    /// Most telling first.
    var flags: [Flag] { flagIDs.map(Flag.init(id:)).sorted { $0.rank < $1.rank } }
    var points: [RiskPoint] {
        timeline.enumerated().compactMap { index, pair in
            pair.count == 2 ? RiskPoint(id: index, t: pair[0], risk: Int(pair[1])) : nil
        }
    }
    var endAction: Action? { actions.last { $0.kind == ShieldAction.Kind.end.rawValue } }
    var warnAction: Action? { actions.last { $0.kind == ShieldAction.Kind.warn.rawValue } }
    /// True when Palisade warned about the call or ended it. A mark on such a call says it was
    /// not a scam; on a call Palisade left alone, that it was one.
    var wasSteppedInto: Bool { outcome == .warned || outcome == .endedByShield }
    /// Only a call that is over can be marked, and only on a server that keeps marks.
    var canBeMarked: Bool { markedWrong != nil && outcome != .inProgress }
}

struct CallList: Decodable, Sendable {
    let calls: [CallSummary]
}

struct ServerHealth: Decodable, Equatable, Sendable {
    let ok: Bool
    var demoMode: Bool?
    var warnThreshold: Int?
    var endThreshold: Int?
    /// "live", or "dry_run" when the server only records what it would have done.
    var actions: String?
    var gateway: GatewayHealth?

    enum CodingKeys: String, CodingKey {
        case ok, actions, gateway
        case demoMode = "demo_mode"
        case warnThreshold = "warn_threshold"
        case endThreshold = "end_threshold"
    }
}

struct DemoScript: Decodable, Identifiable, Hashable, Sendable {
    let id: String
    let label: String
    let notes: String
    let duration: Int

    enum CodingKeys: String, CodingKey {
        case id, label, notes
        case duration = "duration_s"
    }

    var isScam: Bool { label == "scam" }
    /// "scam_05_utility_shutoff" -> "Utility Shutoff", "scam_08_card_otp" -> "Card OTP"
    var title: String {
        let words = id.split(separator: "_").dropFirst(2).map { $0 == "otp" ? "OTP" : $0.capitalized }
        return words.isEmpty ? id : words.joined(separator: " ")
    }
}

struct DemoScriptList: Decodable, Sendable {
    let enabled: Bool
    let scripts: [DemoScript]
}
