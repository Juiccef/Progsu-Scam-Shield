#if DEBUG
import Foundation

// Sample states for previews. Nothing here touches the network.

extension ShieldEvent {
    static func sample(
        _ type: String,
        at offset: Double = 0,
        speaker: String? = nil,
        text: String? = nil,
        highlights: [String]? = nil,
        isFinal: Bool? = nil,
        risk: Int? = nil,
        flags: [String]? = nil,
        kind: String? = nil,
        reason: String? = nil,
        seconds: Double? = nil
    ) -> ShieldEvent {
        ShieldEvent(
            type: type,
            ts: Date.now.timeIntervalSince1970 - 40 + offset,
            callSID: "CAsample",
            speaker: speaker,
            text: text,
            highlights: highlights,
            isFinal: isFinal,
            t: offset,
            risk: risk,
            flags: flags,
            kind: kind,
            reason: reason,
            seconds: seconds
        )
    }
}

extension LiveCall {
    private static let warnReason = "The caller threatened arrest, shutoff or legal action and pressed them to stay on the line."
    private static let endReason = "The caller asked for payment by gift card, wire, crypto or cash and demanded secrecy."

    private static func make(isDemo: Bool = false, _ events: [ShieldEvent]) -> LiveCall {
        let call = LiveCall(id: "CAsample", caller: "***0142", isDemo: isDemo, startedAt: .now.addingTimeInterval(-40))
        events.forEach(call.apply)
        return call
    }

    private static let opening: [ShieldEvent] = [
        .sample("call_answered"),
        .sample("transcript", at: 4, speaker: "CALLER",
                text: "Hello, this is Officer Daniels with the Federal Benefits Office.",
                highlights: ["this is Officer Daniels", "Federal Benefits Office"], isFinal: true),
        .sample("score", at: 5, risk: 24, flags: ["AUTHORITY_IMPERSONATION"]),
        .sample("transcript", at: 7, speaker: "SENIOR", text: "Yes, this is me. What is this about?", isFinal: true),
    ]

    private static let pressure: [ShieldEvent] = [
        .sample("transcript", at: 14, speaker: "CALLER",
                text: "There is a warrant connected to your account. To avoid arrest you must stay on the line.",
                highlights: ["warrant", "arrest", "stay on the line"], isFinal: true),
        .sample("score", at: 15, risk: 72, flags: ["AUTHORITY_IMPERSONATION", "THREAT", "STAY_ON_LINE"]),
        .sample("action", at: 15, risk: 72, kind: "WARN", reason: warnReason),
        .sample("transcript", at: 18, speaker: "SENIOR", text: "Oh my. What do I need to do?", isFinal: true),
    ]

    static var ringing: LiveCall { make([]) }

    static var calm: LiveCall { make(opening) }

    static var watchful: LiveCall {
        make(opening + [
            .sample("transcript", at: 11, speaker: "CALLER", text: "This is urgent. You need to act today.",
                    highlights: ["urgent", "need to act today"], isFinal: true),
            .sample("score", at: 12, risk: 46, flags: ["AUTHORITY_IMPERSONATION", "URGENCY_PRESSURE"]),
        ])
    }

    static var suspicious: LiveCall {
        make(opening + pressure + [
            .sample("transcript", at: 22, speaker: "CALLER", text: "Do not discuss this with anyone, including",
                    highlights: ["Do not discuss this with anyone"], isFinal: false),
        ])
    }

    /// The caller hung up after Shield's warning, before it had grounds to end the call.
    static var warnedThenOver: LiveCall {
        make(opening + pressure + [.sample("call_ended", at: 24)])
    }

    static var endedByShield: LiveCall {
        make(isDemo: false, opening + pressure + [
            .sample("transcript", at: 26, speaker: "CALLER",
                    text: "Do not tell your family. Go and buy gift cards and read me the numbers.",
                    highlights: ["Do not tell your family", "buy gift cards and read me the numbers"], isFinal: true),
            .sample("score", at: 27, risk: 94,
                    flags: ["UNUSUAL_PAYMENT", "SECRECY", "THREAT", "STAY_ON_LINE", "AUTHORITY_IMPERSONATION"]),
            .sample("action", at: 27, risk: 94, kind: "END", reason: endReason),
            .sample("action_result", at: 27.3, kind: "END", seconds: 2.4),
            .sample("call_ended", at: 30),
        ])
    }
}

extension CallSummary {
    static func sample(
        id: String,
        minutesAgo: Double,
        outcome: Outcome,
        peakRisk: Int,
        flags: [String] = [],
        actions: [Action] = [],
        timeline: [[Double]] = [],
        isDemo: Bool = false
    ) -> CallSummary {
        CallSummary(
            id: id,
            startedAtSeconds: Date.now.timeIntervalSince1970 - minutesAgo * 60,
            caller: "***\(1000 + id.count * 137)",
            isDemo: isDemo,
            outcome: outcome,
            duration: 48,
            peakRisk: peakRisk,
            flagIDs: flags,
            actions: actions,
            timeline: timeline
        )
    }

    static let blocked = CallSummary.sample(
        id: "CAblocked",
        minutesAgo: 12,
        outcome: .endedByShield,
        peakRisk: 94,
        flags: ["UNUSUAL_PAYMENT", "SECRECY", "THREAT", "AUTHORITY_IMPERSONATION"],
        actions: [
            Action(kind: "WARN", risk: 72,
                   reason: "The caller threatened arrest, shutoff or legal action and pressed them to stay on the line.",
                   at: nil, seconds: 1.2),
            Action(kind: "END", risk: 94,
                   reason: "The caller asked for payment by gift card, wire, crypto or cash and demanded secrecy.",
                   at: nil, seconds: 2.4),
        ],
        timeline: [[3, 20], [8, 24], [14, 35], [19, 72], [24, 78], [30, 88], [33, 94]]
    )

    static let samples: [CallSummary] = [
        blocked,
        .sample(id: "CAwarned", minutesAgo: 190, outcome: .warned, peakRisk: 68, flags: ["THREAT", "URGENCY_PRESSURE"]),
        .sample(id: "CApharmacy", minutesAgo: 1500, outcome: .completed, peakRisk: 5),
        .sample(id: "CAdoctor", minutesAgo: 2900, outcome: .completed, peakRisk: 2),
    ]
}

extension ShieldStore {
    /// The store behind the `-sample <name>` launch argument: one fixed screen, no server.
    static func sample(named name: String) -> ShieldStore? {
        switch name {
        case "home": return preview(history: CallSummary.samples)
        case "home-empty": return preview()
        case "home-offline": return preview(connection: .failed("Can’t reach the Shield server."))
        case "home-call": return preview(call: .suspicious, history: CallSummary.samples)
        case "ringing": return preview(call: .ringing, isShowingCall: true)
        case "calm": return preview(call: .calm, isShowingCall: true)
        case "watchful": return preview(call: .watchful, isShowingCall: true)
        case "suspicious": return preview(call: .suspicious, isShowingCall: true)
        case "warned-over": return preview(call: .warnedThenOver, isShowingCall: true)
        case "ended": return preview(call: .endedByShield, isShowingCall: true)
        case "detail":
            let store = preview(history: CallSummary.samples)
            store.path = [.blocked]
            return store
        default: return nil
        }
    }
}
#endif
