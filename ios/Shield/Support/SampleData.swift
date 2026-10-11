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

    /// The caller hung up after Palisade's warning, before it had grounds to end the call.
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

extension GatewayMessage {
    static func sample(
        id: String,
        minutesAgo: Double,
        sender: String,
        status: String,
        risk: Int?,
        label: String?,
        flags: [String] = [],
        hold: String? = nil,
        explanation: String = "No strong scam signals detected. This does not verify the sender or any links.",
        words: String? = nil,
        from wholeSender: String? = nil
    ) -> GatewayMessage {
        let arrived = Date.now.timeIntervalSince1970 - minutesAgo * 60
        return GatewayMessage(
            messageID: id,
            ts: arrived,
            sender: sender,
            status: status,
            risk: risk,
            label: label,
            flags: flags,
            holdReason: hold,
            expiresAt: arrived + 24 * 60 * 60,
            reviewEligible: status == "held" && hold != "unsupported_content",
            explanation: explanation,
            text: words,
            originalSender: wholeSender
        )
    }

    /// The same text as a list has it: without its words or its sender's whole number.
    var listed: GatewayMessage {
        .init(
            messageID: messageID, ts: ts, sender: sender, status: status, risk: risk, label: label, flags: flags,
            holdReason: holdReason, expiresAt: expiresAt, reviewEligible: reviewEligible, explanation: explanation,
            text: nil, originalSender: nil
        )
    }

    /// The text after the caregiver's decision, the way the server answers it.
    func sampleDecided(_ action: String) -> GatewayMessage {
        let released = action == "release"
        return .init(
            messageID: messageID, ts: ts, sender: sender, status: released ? "queued" : "discarded", risk: risk,
            label: label, flags: flags, holdReason: holdReason, expiresAt: expiresAt, reviewEligible: false,
            explanation: explanation, text: released ? text : nil, originalSender: released ? originalSender : nil
        )
    }

    static let heldFee = GatewayMessage.sample(
        id: "twilio_SMfee",
        minutesAgo: 4,
        sender: "***0199",
        status: "held",
        risk: 81,
        label: "likely_scam",
        flags: ["PRIZE_OR_REFUND", "URGENCY_PRESSURE"],
        hold: "scam_signals",
        explanation: "Verify any unexpected fee, prize or refund through the organization’s official app or website.",
        words: "USPS: Your package is on hold. Pay the $1.99 redelivery fee today at https://usps-redelivery.example/pay or it will be returned.",
        from: "+14045550199"
    )

    static let heldCode = GatewayMessage.sample(
        id: "twilio_SMcode",
        minutesAgo: 38,
        sender: "***0164",
        status: "held",
        risk: 55,
        label: "suspicious",
        flags: ["OTP_REQUEST", "FAMILY_EMERGENCY"],
        hold: "scam_signals",
        explanation: "Verify independently before sharing account details or verification codes.",
        words: "Hi Grandma, I dropped my phone and this is my new number. The bank is texting you a code, can you send it to me?",
        from: "+14045550164"
    )

    static let delivered = GatewayMessage.sample(
        id: "twilio_SMvisit",
        minutesAgo: 190,
        sender: "***0142",
        status: "delivered",
        risk: 0,
        label: "safe",
        from: "+14045550142"
    )

    /// Texts as the server has them, with their words, newest first.
    static let wholeSamples: [GatewayMessage] = [
        heldFee,
        heldCode,
        delivered,
        .sample(id: "twilio_SMpharmacy", minutesAgo: 1500, sender: "***0117", status: "sent", risk: 4, label: "safe"),
        .sample(
            id: "twilio_SMprize", minutesAgo: 2900, sender: "***0188", status: "discarded", risk: 88, label: "likely_scam",
            flags: ["PRIZE_OR_REFUND", "UNUSUAL_PAYMENT"], hold: "scam_signals",
            explanation: "Verify any unexpected fee, prize or refund through the organization’s official app or website."
        ),
    ]

    static let samples = wholeSamples.map(\.listed)
}

extension MessageSummary {
    static func sample(id: String, minutesAgo: Double, pasted: Bool, risk: Int, label: String, flags: [String] = []) -> MessageSummary {
        MessageSummary(
            messageID: id,
            ts: Date.now.timeIntervalSince1970 - minutesAgo * 60,
            sender: pasted ? "unknown" : "***0\(100 + id.count * 7)",
            source: pasted ? "manual" : "twilio",
            risk: risk,
            label: label,
            flags: flags,
            explanation: nil,
            textOnly: false
        )
    }

    static let pasted: [MessageSummary] = [
        .sample(id: "manual_bank", minutesAgo: 75, pasted: true, risk: 74, label: "likely_scam",
                flags: ["CREDENTIAL_REQUEST", "URGENCY_PRESSURE"]),
        .sample(id: "manual_dentist", minutesAgo: 3100, pasted: true, risk: 0, label: "safe"),
    ]

    /// Texts a server with text protection off scored as they arrived.
    static let scored: [MessageSummary] = [
        .sample(id: "SMtoll", minutesAgo: 22, pasted: false, risk: 78, label: "likely_scam",
                flags: ["PRIZE_OR_REFUND", "URGENCY_PRESSURE"]),
        .sample(id: "SMreminder", minutesAgo: 260, pasted: false, risk: 0, label: "safe"),
    ]
}

extension GatewayHealth {
    static let on = GatewayHealth(enabled: true, forwardingEnabled: true, connected: true, smsReady: true)
    /// Checking and holding, with forwarding switched off and no alert service running: a server being set up.
    static let paused = GatewayHealth(enabled: true, forwardingEnabled: false, connected: false, smsReady: false)
    static let off = GatewayHealth(enabled: false, forwardingEnabled: nil, connected: nil, smsReady: nil)
    /// Forwarding by iMessage, with the agent checking in. SMS is not set up at all, as on a server that never needed it.
    static let iMessage = GatewayHealth(
        enabled: true, forwardingEnabled: true, connected: true, smsReady: false, forwardVia: "imessage", forwardingReady: true
    )
    /// Forwarding by iMessage, with no agent checking in.
    static let iMessageDown = GatewayHealth(
        enabled: true, forwardingEnabled: true, connected: false, smsReady: false, forwardVia: "imessage", forwardingReady: false
    )
}

extension ShieldStore {
    /// The number people text in the samples. Like the callers', it is not a real one.
    private static let sampleNumber = "+14045550100"

    /// The store behind the `-sample <name>` launch argument: one fixed screen, no server.
    static func sample(named name: String) -> ShieldStore? {
        switch name {
        case "home": return preview(history: CallSummary.samples)
        case "home-empty": return preview()
        case "home-offline": return preview(connection: .failed("Can’t reach the Palisade server."))
        // A server that asks each caregiver to sign in. Settings shows the account on these three.
        case "home-signed-in":
            return preview(history: CallSummary.samples, offersSignIn: true, account: "daughter@example.com")
        case "home-signed-out": return preview(connection: .signedOut, offersSignIn: true)
        case "home-refused":
            return preview(
                connection: .refused("This account is not on the caregiver list."),
                offersSignIn: true,
                account: "neighbor@example.com"
            )
        case "home-call": return preview(call: .suspicious, history: CallSummary.samples)
        case "ringing": return preview(call: .ringing, isShowingCall: true)
        case "calm": return preview(call: .calm, isShowingCall: true)
        case "watchful": return preview(call: .watchful, isShowingCall: true)
        case "suspicious": return preview(call: .suspicious, isShowingCall: true)
        case "warned-over": return preview(call: .warnedThenOver, isShowingCall: true)
        case "ended": return preview(call: .endedByShield, isShowingCall: true)
        case "calls":
            let store = preview(history: CallSummary.samples)
            store.tab = .calls
            return store
        case "signs":
            let store = preview(history: CallSummary.samples)
            store.tab = .signs
            return store
        case "detail":
            let store = preview(history: CallSummary.samples)
            store.tab = .calls
            store.callsPath = [.blocked]
            return store
        case "texts", "texts-paused", "texts-imessage", "texts-imessage-down", "text-held", "text-released", "text-delivered",
             "text-sent-imessage", "alerts", "home-texts":
            // "text-released" is the first sample text after the caregiver let it through.
            let whole = name == "text-released"
                ? [GatewayMessage.heldFee.sampleDecided("release")] + GatewayMessage.wholeSamples.dropFirst()
                : GatewayMessage.wholeSamples
            let protection: GatewayHealth = switch name {
            case "texts-paused": .paused
            case "texts-imessage", "text-sent-imessage": .iMessage
            case "texts-imessage-down": .iMessageDown
            default: .on
            }
            let store = preview(history: CallSummary.samples).withTexts(
                whole.map(\.listed),
                whole: whole,
                checks: MessageSummary.pasted,
                protection: protection,
                number: sampleNumber
            )
            switch name {
            case "home-texts": store.tab = .home
            case "alerts": store.tab = .alerts
            default: store.tab = .texts
            }
            if name == "text-held" || name == "text-released" { store.textsPath = [.text(GatewayMessage.heldFee.id)] }
            if name == "text-delivered" { store.textsPath = [.text(GatewayMessage.delivered.id)] }
            if name == "text-sent-imessage" { store.textsPath = [.text("twilio_SMpharmacy")] }
            return store
        case "texts-empty":
            let store = preview(history: CallSummary.samples).withTexts(protection: .on, number: sampleNumber)
            store.tab = .texts
            return store
        // A server that only scores the texts sent to its number.
        case "texts-off":
            let store = preview(history: CallSummary.samples)
                .withTexts(checks: MessageSummary.scored + MessageSummary.pasted, protection: .off)
            store.tab = .texts
            return store
        default: return nil
        }
    }
}
#endif
