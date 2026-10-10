import SwiftUI

/// The full-screen view of a call in progress. It becomes the "Shield ended the call" screen
/// the moment Shield hangs up on a scammer.
struct LiveCallView: View {
    let call: LiveCall

    @Environment(ShieldStore.self) private var store
    @AppStorage(SettingsKey.seniorName) private var seniorName = SettingsKey.defaultSeniorName

    var body: some View {
        VStack(spacing: 0) {
            CallHeader(call: call, seniorName: seniorName)
            if call.ending != nil {
                ScrollView {
                    ShieldEndedContent(call: call, seniorName: seniorName)
                }
                // The buttons stay on screen however long the explanation above them gets.
                .safeAreaInset(edge: .bottom, spacing: 0) {
                    ShieldEndedActions(call: call, seniorName: seniorName)
                }
                // Its pieces arrive one after another (see `arrives`); the screen itself is just there.
                .transition(.identity)
            } else {
                CallMonitor(call: call, seniorName: seniorName)
                    // Out of the way first, and quickly: the two screens are never drawn over each other.
                    .transition(.asymmetric(insertion: .opacity, removal: .opacity.animation(Motion.quick)))
            }
        }
        .background(Theme.canvas.ignoresSafeArea())
        .animation(Motion.screen, value: call.ending != nil)
        .task(id: call.hasEnded) {
            // An ordinary call that ended without incident does not need to stay on screen.
            guard call.hasEnded, call.ending == nil, call.warning == nil else { return }
            try? await Task.sleep(for: .seconds(4))
            if !Task.isCancelled, store.presentedCall?.id == call.id {
                store.closeCall()
            }
        }
        .accessibilityAddTraits(.isModal)
    }
}

// MARK: - Header

private struct CallHeader: View {
    let call: LiveCall
    let seniorName: String

    @Environment(ShieldStore.self) private var store
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        let isOver = call.hasEnded || call.ending != nil
        HStack(spacing: 12) {
            VStack(alignment: .leading, spacing: 3) {
                Text("Call to \(seniorName)")
                    .font(.title3.weight(.bold))
                CallStatusLine(call: call)
            }
            Spacer(minLength: 8)
            if isOver {
                Button("Done") {
                    store.closeCall()
                }
                .fontWeight(.semibold)
                .headerButton(.capsule)
                .replaces(reduceMotion: reduceMotion)
            } else {
                // The call goes on; this only puts its screen away.
                Button("Hide call", systemImage: "chevron.down") {
                    store.closeCall()
                }
                .labelStyle(.iconOnly)
                .fontWeight(.semibold)
                .headerButton(.circle)
                .replaces(reduceMotion: reduceMotion)
            }
        }
        .padding(.horizontal, 16)
        .padding(.top, 12)
        .padding(.bottom, 6)
        .animation(Motion.change, value: isOver)
    }
}

private struct CallStatusLine: View {
    let call: LiveCall

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        // The line takes a few seconds to drop after Shield hangs up. Don't call that "Live".
        let phase = call.ending == nil ? call.phase : .ended
        HStack(spacing: 6) {
            HStack(spacing: 6) {
                switch phase {
                case .ringing:
                    Text("Ringing")
                case .talking:
                    Circle()
                        .fill(Theme.danger)
                        .frame(width: 8, height: 8)
                        .accessibilityHidden(true)
                    Text("Live")
                    if let answeredAt = call.answeredAt {
                        Text(timerInterval: answeredAt...Date.distantFuture, countsDown: false)
                            .monospacedDigit()
                    }
                case .ended:
                    Text("Call ended")
                }
            }
            .id(phase)
            .replaces(reduceMotion: reduceMotion)
            Text("·").accessibilityHidden(true)
            Text(callerDescription(call.caller, isDemo: call.isDemo))
        }
        .font(.subheadline)
        .foregroundStyle(.secondary)
        .lineLimit(1)
        .animation(Motion.change, value: phase)
    }
}

/// Who called, in words, the same on every screen. The server masks numbers to "***1234"; a
/// demo call has no real caller, and saying so here is how every screen marks it as a demo.
func callerDescription(_ masked: String, isDemo: Bool = false) -> String {
    if isDemo { return "Demo call" }
    let digits = masked.filter(\.isNumber)
    return digits.isEmpty ? "Unknown number" : "Number ending \(digits)"
}

// MARK: - While the call is going

private struct CallMonitor: View {
    let call: LiveCall
    let seniorName: String

    var body: some View {
        VStack(spacing: 0) {
            // The dial gets its full size first. On a small screen it gives way only once the
            // transcript is down to a few lines.
            RiskPanel(call: call)
                .layoutPriority(1)
            if let warning = call.warning {
                WarningCard(warning: warning, seniorName: seniorName)
                    .transition(Unfold())
                    .padding(.horizontal, 16)
                    .padding(.bottom, 12)
            }
            TranscriptArea(call: call, seniorName: seniorName)
                .frame(minHeight: 120)
            if call.hasEnded {
                CallOverBar(call: call)
                    // Up from the bottom edge, in step with the transcript making room above it.
                    .transition(.move(edge: .bottom).combined(with: .opacity))
            }
        }
        // Declared on the stack, not on the piece that changes, so that what arrives and what
        // makes room for it move as one. The warning waits a moment for the signs under the
        // dial, which it replaces, to clear; listed first, it is the one used when Shield warns.
        .animation(Motion.change.delay(Motion.lead), value: call.warning != nil)
        .animation(Motion.change, value: call.rankedFlags)
        .animation(Motion.change, value: call.hasEnded)
    }
}

/// The dial, and under it the warning signs heard so far, most telling first.
private struct RiskPanel: View {
    let call: LiveCall

    @Environment(ShieldStore.self) private var store

    var body: some View {
        VStack(spacing: 10) {
            RiskDial(risk: call.risk, warnAt: store.warnThreshold, endAt: store.endThreshold)
                .frame(maxWidth: 196)
            // Once Shield has warned, its warning says why. Until then these lines do.
            if call.warning == nil, !call.rankedFlags.isEmpty {
                VStack(spacing: 2) {
                    ForEach(call.rankedFlags) { flag in
                        Text(flag.title)
                    }
                }
                .font(.footnote)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 24)
                .accessibilityElement(children: .ignore)
                .accessibilityLabel("Warning signs: \(call.rankedFlags.map(\.title).formatted(.list(type: .and)))")
                .transition(.asymmetric(insertion: .opacity, removal: .opacity.animation(Motion.quick)))
            }
        }
        .padding(.bottom, 14)
    }
}

/// Shield's warning, with the one thing the caregiver can do about it: ring the person being called.
private struct WarningCard: View {
    let warning: ShieldAction
    let seniorName: String

    var body: some View {
        Card {
            VStack(alignment: .leading, spacing: 12) {
                VStack(alignment: .leading, spacing: 6) {
                    CardLabel(title: "Warning", symbol: "exclamationmark.triangle.fill", tint: Theme.caution)
                    Text("This call looks suspicious")
                        .font(.headline)
                        .fixedSize(horizontal: false, vertical: true)
                    Text(warning.reason)
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .accessibilityElement(children: .combine)
                CallSeniorButton(seniorName: seniorName)
                    .controlSize(.large)
            }
        }
    }
}

private struct TranscriptArea: View {
    let call: LiveCall
    let seniorName: String

    @Environment(ShieldStore.self) private var store

    var body: some View {
        if call.lines.isEmpty {
            VStack(spacing: 8) {
                Image(systemName: call.phase == .ringing ? "phone.arrow.down.left" : "waveform")
                    .font(.title)
                    .symbolEffect(.pulse)
                    .accessibilityHidden(true)
                Text(call.phase == .ringing ? "Waiting for \(seniorName) to pick up" : "Listening")
                    .font(.subheadline)
            }
            .foregroundStyle(.secondary)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        } else {
            let level = RiskLevel(risk: call.risk, warnAt: store.warnThreshold, endAt: store.endThreshold)
            TranscriptView(lines: call.lines, seniorName: seniorName, markColor: level.markColor)
        }
    }
}

/// Shown when a call ends on its own, without Shield stepping in.
private struct CallOverBar: View {
    let call: LiveCall

    var body: some View {
        let wasWarned = call.warning != nil
        Card {
            VStack(alignment: .leading, spacing: 4) {
                CardLabel(
                    title: "Call ended",
                    symbol: wasWarned ? "exclamationmark.triangle.fill" : "checkmark.circle.fill",
                    tint: wasWarned ? Theme.caution : Theme.safe
                )
                Text(wasWarned ? "Shield warned you during this call." : "Nothing suspicious was detected.")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            }
        }
        .padding(.horizontal, 16)
        .padding(.bottom, 12)
        .accessibilityElement(children: .combine)
    }
}

// MARK: - After Shield ended the call

struct ShieldEndedContent: View {
    let call: LiveCall
    let seniorName: String

    @State private var hasArrived = false
    /// Set a beat after `hasArrived`, once the shield is there to be seen bouncing.
    @State private var shieldHasLanded = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    // Explicit so the view's inputs stay clear of its private state on every SDK.
    init(call: LiveCall, seniorName: String) {
        self.call = call
        self.seniorName = seniorName
    }

    var body: some View {
        VStack(spacing: 12) {
            VStack(spacing: 8) {
                Image(systemName: "checkmark.shield.fill")
                    .font(.system(size: 56))
                    // The system's own soft gradient, the way it shades a symbol this large.
                    .foregroundStyle(Theme.safe.gradient)
                    .symbolEffect(.bounce, value: shieldHasLanded)
                    .accessibilityHidden(true)
                    .arrives(0, when: hasArrived)
                VStack(spacing: 8) {
                    Text("Shield ended the call")
                        .font(.largeTitle.weight(.bold))
                        .multilineTextAlignment(.center)
                    Text(explanation)
                        .foregroundStyle(.secondary)
                        .multilineTextAlignment(.center)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .arrives(1, when: hasArrived)
            }
            .padding(.horizontal, 8)
            .padding(.top, 28)
            .padding(.bottom, 16)

            if let ending = call.ending {
                VStack(spacing: 12) {
                    ResponseTime(seconds: ending.seconds)
                    Card {
                        VStack(alignment: .leading, spacing: 6) {
                            CardLabel(title: "Why")
                            // The one sentence says it. Each sign on its own is in "What happened".
                            Text(ending.reason)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    .accessibilityElement(children: .combine)
                }
                .arrives(2, when: hasArrived)
                // The response time is measured a moment after the hang-up. "Why" makes room for it.
                .animation(Motion.change, value: ending.seconds)
            }
        }
        .padding(.horizontal, 16)
        .padding(.bottom, 16)
        .task {
            hasArrived = true
            guard !reduceMotion else { return }
            try? await Task.sleep(for: .seconds(Motion.lead))
            shieldHasLanded = true
        }
    }

    private var explanation: String {
        if call.isDemo {
            "On a real call, \(seniorName) would hear a short safety message and the caller would be disconnected."
        } else {
            "\(seniorName) heard a short safety message, then the caller was disconnected."
        }
    }
}

/// What the caregiver can do next. Pinned under the explanation so it never scrolls out of reach.
/// Leaving the screen is the header's Done button.
private struct ShieldEndedActions: View {
    let call: LiveCall
    let seniorName: String

    @Environment(ShieldStore.self) private var store
    @State private var hasArrived = false

    var body: some View {
        // Side by side at one width while both labels fit; stacked for a long name or large text.
        ViewThatFits(in: .horizontal) {
            EqualWidthHStack(spacing: 10) { buttons }
            VStack(spacing: 8) { buttons }
        }
        .controlSize(.large)
        .arrives(3, when: hasArrived)
        .padding(.horizontal, 16)
        .padding(.top, 10)
        .padding(.bottom, 8)
        .background(Theme.canvas)
        .onAppear { hasArrived = true }
    }

    @ViewBuilder private var buttons: some View {
        CallSeniorButton(seniorName: seniorName)
        Button {
            Task { await store.showSummary(of: call.id) }
        } label: {
            Text("What happened")
                .lineLimit(1)
                .frame(maxWidth: .infinity)
        }
        .buttonStyle(QuietButtonStyle())
    }
}

/// How quickly Shield acted, once the server has measured it.
private struct ResponseTime: View {
    let seconds: Double?

    var body: some View {
        if let seconds {
            Card {
                VStack(alignment: .leading, spacing: 2) {
                    CardLabel(title: "Response time")
                        .padding(.bottom, 2)
                    HStack(alignment: .firstTextBaseline, spacing: 4) {
                        Text(seconds, format: .number.precision(.fractionLength(1)))
                            .font(.system(.largeTitle, design: .rounded, weight: .semibold))
                            .monospacedDigit()
                        Text("sec")
                            .font(.headline)
                            .foregroundStyle(.secondary)
                    }
                    Text("From the caller’s request to the hang-up.")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
            }
            .accessibilityElement(children: .combine)
            .transition(Unfold())
        }
    }
}

// MARK: - Arriving in order

/// Brings in one piece of the "Shield ended the call" screen, a beat after the piece above it.
/// This is the one moment in the app that is given time: it is what the caregiver was waiting for.
private struct Arrives: ViewModifier {
    let order: Int
    let hasArrived: Bool

    func body(content: Content) -> some View {
        content
            .opacity(hasArrived ? 1 : 0)
            // Nothing arrives until the live screen has cleared.
            .animation(Motion.change.delay(Motion.lead + Double(order) * 0.07), value: hasArrived)
    }
}

private extension View {
    func arrives(_ order: Int, when hasArrived: Bool) -> some View {
        modifier(Arrives(order: order, hasArrived: hasArrived))
    }
}

// MARK: - Calling the person Shield protects

/// Dials the protected person. Shows nothing until their number has been added in Settings.
struct CallSeniorButton: View {
    let seniorName: String

    @Environment(\.openURL) private var openURL
    @AppStorage(SettingsKey.seniorPhone) private var seniorPhone = ""

    // Explicit so the view's inputs stay clear of its private state on every SDK.
    init(seniorName: String) {
        self.seniorName = seniorName
    }

    var body: some View {
        if let phoneURL {
            Button {
                openURL(phoneURL)
            } label: {
                Label("Call \(seniorName)", systemImage: "phone.fill")
                    .lineLimit(1)
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
        }
    }

    private var phoneURL: URL? {
        let digits = seniorPhone.filter { $0.isNumber || $0 == "+" }
        return digits.count >= 7 ? URL(string: "tel://\(digits)") : nil
    }
}
