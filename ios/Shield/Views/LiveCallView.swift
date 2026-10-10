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
                .transition(.opacity.combined(with: .scale(scale: 0.96)))
            } else {
                CallMonitor(call: call, seniorName: seniorName)
                    .transition(.opacity)
            }
        }
        .background(Theme.canvas.ignoresSafeArea())
        .animation(.smooth(duration: 0.45), value: call.ending != nil)
        .sensoryFeedback(.warning, trigger: call.warning?.at)
        .sensoryFeedback(.success, trigger: call.ending?.at)
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

    var body: some View {
        HStack(spacing: 12) {
            VStack(alignment: .leading, spacing: 3) {
                Text("Call to \(seniorName)")
                    .font(.title3.weight(.bold))
                CallStatusLine(call: call)
            }
            Spacer(minLength: 8)
            if call.hasEnded || call.ending != nil {
                Button("Done") {
                    store.closeCall()
                }
                .fontWeight(.semibold)
                .buttonStyle(QuietButtonStyle(height: 44))
            } else {
                // The call goes on; this only puts its screen away.
                Button {
                    store.closeCall()
                } label: {
                    Image(systemName: "chevron.down")
                        .font(.body.weight(.semibold))
                        .frame(width: 44, height: 44)
                        .background(Theme.surface, in: .circle)
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Hide call")
            }
        }
        .padding(.horizontal, 16)
        .padding(.top, 12)
        .padding(.bottom, 6)
    }
}

private struct CallStatusLine: View {
    let call: LiveCall

    var body: some View {
        HStack(spacing: 6) {
            // The line takes a few seconds to drop after Shield hangs up. Don't call that "Live".
            switch call.ending == nil ? call.phase : .ended {
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
            Text("·").accessibilityHidden(true)
            Text(callerDescription(call.caller, isDemo: call.isDemo))
        }
        .font(.subheadline)
        .foregroundStyle(.secondary)
        .lineLimit(1)
    }
}

/// Who called, in words. The server masks numbers to "***1234"; a demo call has no real caller,
/// and saying so here is how every screen marks it as a demo.
func callerDescription(_ masked: String, isDemo: Bool = false) -> String {
    if isDemo { return "Demo call" }
    let digits = masked.filter(\.isNumber)
    return digits.isEmpty ? "Unknown number" : "Number ending \(digits)"
}

/// The caller as a phone lists one: the masked number written like a phone number.
func callerNumber(_ masked: String, isDemo: Bool = false) -> String {
    if isDemo { return "Demo call" }
    let digits = masked.filter(\.isNumber)
    return digits.isEmpty ? "Unknown caller" : "(•••) •••-\(digits)"
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
            WarningArea(call: call, seniorName: seniorName)
            TranscriptArea(call: call, seniorName: seniorName)
                .frame(minHeight: 120)
            if call.hasEnded {
                CallOverBar(call: call)
                    .transition(.move(edge: .bottom).combined(with: .opacity))
            }
        }
        .animation(.smooth, value: call.hasEnded)
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
                .transition(.opacity)
            }
        }
        .padding(.bottom, 14)
        .animation(.smooth, value: call.rankedFlags)
        .animation(.smooth, value: call.warning != nil)
    }
}

/// Shield's warning, with the one thing the caregiver can do about it: ring the person being called.
private struct WarningArea: View {
    let call: LiveCall
    let seniorName: String

    var body: some View {
        VStack(spacing: 0) {
            if let warning = call.warning {
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
                .padding(.horizontal, 16)
                .padding(.bottom, 12)
                .transition(.move(edge: .top).combined(with: .opacity))
            }
        }
        .animation(.spring(duration: 0.45, bounce: 0.2), value: call.warning != nil)
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

    @State private var hasAppeared = false

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
                    .foregroundStyle(Theme.safe)
                    .symbolEffect(.bounce, value: hasAppeared)
                    .accessibilityHidden(true)
                Text("Shield ended the call")
                    .font(.largeTitle.weight(.bold))
                    .multilineTextAlignment(.center)
                Text(explanation)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .padding(.horizontal, 8)
            .padding(.top, 28)
            .padding(.bottom, 16)

            if let ending = call.ending {
                ResponseTime(seconds: ending.seconds)
                Card {
                    VStack(alignment: .leading, spacing: 6) {
                        CardLabel(title: "Why", symbol: "exclamationmark.triangle.fill", tint: Theme.danger)
                        // The one sentence says it. Each sign on its own is in "What happened".
                        Text(ending.reason)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                .accessibilityElement(children: .combine)
            }
        }
        .padding(.horizontal, 16)
        .padding(.bottom, 16)
        .onAppear { hasAppeared = true }
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

    var body: some View {
        // Side by side at one width while both labels fit; stacked for a long name or large text.
        ViewThatFits(in: .horizontal) {
            EqualWidthHStack(spacing: 10) { buttons }
            VStack(spacing: 8) { buttons }
        }
        .controlSize(.large)
        .padding(.horizontal, 16)
        .padding(.top, 10)
        .padding(.bottom, 8)
        .background(Theme.canvas)
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
                    CardLabel(title: "Response time", symbol: "timer", tint: Theme.safe)
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
        }
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
                    .foregroundStyle(Theme.onTint)
            }
            .buttonStyle(.borderedProminent)
        }
    }

    private var phoneURL: URL? {
        let digits = seniorPhone.filter { $0.isNumber || $0 == "+" }
        return digits.count >= 7 ? URL(string: "tel://\(digits)") : nil
    }
}
