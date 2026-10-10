import Foundation
import Observation

/// One line of the live transcript. An in-progress line is replaced as the speaker keeps talking.
struct TranscriptLine: Identifiable, Equatable, Sendable {
    enum Speaker: String, Sendable {
        case caller = "CALLER"
        case senior = "SENIOR"
    }

    let id: Int
    let speaker: Speaker
    var text: String
    var isFinal: Bool
    /// True when the previous line was the other speaker's.
    var startsTurn = true
    /// Phrases in `text` that Palisade flagged. Only ever set on the caller's lines.
    var highlights: [String] = []
}

struct RiskPoint: Identifiable, Equatable, Sendable {
    let id: Int
    let t: Double
    let risk: Int
}

/// The call in progress, rebuilt event by event from the server's feed.
///
/// Each property is observed separately, so the dial redraws on `risk` alone and the
/// transcript on `lines` alone.
@Observable
@MainActor
final class LiveCall: Identifiable {
    enum Phase: Equatable {
        case ringing
        case talking
        case ended
    }

    nonisolated let id: String
    let caller: String
    let isDemo: Bool
    let startedAt: Date

    private(set) var phase: Phase = .ringing
    private(set) var answeredAt: Date?
    private(set) var risk = 0
    /// In the order they were detected.
    private(set) var flags: [Flag] = [] {
        didSet { rankedFlags = flags.sorted { $0.rank < $1.rank } }
    }
    /// Most telling first.
    private(set) var rankedFlags: [Flag] = []
    private(set) var lines: [TranscriptLine] = []
    /// True once the first words are in. Until then Palisade has heard nothing to judge, and
    /// the screens say so rather than call the call normal.
    private(set) var hasSpeech = false
    private(set) var timeline: [RiskPoint] = []
    private(set) var warning: ShieldAction?
    /// Set when Palisade ended the call.
    private(set) var ending: ShieldAction?
    private(set) var duration: TimeInterval?

    @ObservationIgnored private var nextLineID = 0
    @ObservationIgnored private var openLine: [TranscriptLine.Speaker: Int] = [:]

    init(id: String, caller: String, isDemo: Bool, startedAt: Date) {
        self.id = id
        self.caller = caller
        self.isDemo = isDemo
        self.startedAt = startedAt
    }

    var hasEnded: Bool { phase == .ended }

    func apply(_ event: ShieldEvent) {
        switch event.type {
        case "call_answered":
            answeredAt = event.date
            if phase == .ringing { phase = .talking }
        case "transcript":
            if phase == .ringing { phase = .talking }
            addTranscript(event)
        case "rule_hit":
            merge(flags: event.flags)
            // The rules answer before the model does: move the dial now rather than a second later.
            risk = max(risk, event.ruleRisk ?? 0)
        case "score":
            merge(flags: event.flags)
            if let newRisk = event.risk {
                risk = newRisk
                timeline.append(RiskPoint(id: timeline.count, t: event.t ?? 0, risk: newRisk))
            }
        case "action":
            guard let kind = event.kind.flatMap(ShieldAction.Kind.init(rawValue:)) else { return }
            let action = ShieldAction(kind: kind, risk: event.risk ?? risk, reason: event.reason ?? "", at: event.date)
            merge(flags: event.flags)
            switch kind {
            case .warn: warning = action
            case .end: ending = action
            }
        case "action_result":
            if event.kind == ShieldAction.Kind.end.rawValue {
                ending?.seconds = event.seconds
            } else {
                warning?.seconds = event.seconds
            }
        case "call_ended":
            phase = .ended
            duration = event.duration
            closeOpenLines()
        default:
            break
        }
    }

    private func merge(flags ids: [String]?) {
        for id in ids ?? [] where !flags.contains(where: { $0.id == id }) {
            flags.append(Flag(id: id))
        }
    }

    private func addTranscript(_ event: ShieldEvent) {
        guard let speaker = event.speaker.flatMap(TranscriptLine.Speaker.init(rawValue:)),
              let text = event.text, !text.isEmpty else { return }
        let isFinal = event.isFinal ?? true
        if let id = openLine[speaker], let index = lines.lastIndex(where: { $0.id == id }) {
            lines[index].text = text
            lines[index].isFinal = isFinal
            lines[index].highlights = event.highlights ?? []
        } else {
            let startsTurn = lines.last?.speaker != speaker
            lines.append(
                TranscriptLine(
                    id: nextLineID,
                    speaker: speaker,
                    text: text,
                    isFinal: isFinal,
                    startsTurn: startsTurn,
                    highlights: event.highlights ?? []
                )
            )
            openLine[speaker] = nextLineID
            nextLineID += 1
            if !hasSpeech { hasSpeech = true }
        }
        if isFinal { openLine[speaker] = nil }
    }

    private func closeOpenLines() {
        for index in lines.indices where !lines[index].isFinal {
            lines[index].isFinal = true
        }
        openLine.removeAll()
    }
}
