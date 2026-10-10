#if DEBUG
import SwiftUI

// Xcode previews for every screen, built from SampleData. None of them touches the network.

#Preview("Summary") {
    MainTabs()
        .environment(ShieldStore.preview(history: CallSummary.samples))
}

#Preview("Summary, no calls yet") {
    MainTabs()
        .environment(ShieldStore.preview())
}

#Preview("Summary, not connected") {
    MainTabs()
        .environment(ShieldStore.preview(connection: .failed("Can’t reach the Palisade server.")))
}

#Preview("Summary, not signed in") {
    MainTabs()
        .environment(ShieldStore.sample(named: "home-signed-out") ?? .preview())
}

#Preview("Settings, signed in") {
    SettingsView()
        .environment(ShieldStore.sample(named: "home-signed-in") ?? .preview())
}

#Preview("Calls") {
    MainTabs()
        .environment(ShieldStore.sample(named: "calls") ?? .preview())
}

#Preview("Summary, texts to review") {
    MainTabs()
        .environment(ShieldStore.sample(named: "home-texts") ?? .preview())
}

#Preview("Notifications") {
    MainTabs()
        .environment(ShieldStore.sample(named: "notifications") ?? .preview())
}

#Preview("Texts") {
    MainTabs()
        .environment(ShieldStore.sample(named: "texts") ?? .preview())
}

#Preview("Texts, protection off") {
    MainTabs()
        .environment(ShieldStore.sample(named: "texts-off") ?? .preview())
}

#Preview("Held text") {
    MainTabs()
        .environment(ShieldStore.sample(named: "text-held") ?? .preview())
}

#Preview("Check a Text") {
    CheckTextView()
        .environment(ShieldStore.preview())
}

#Preview("Scam Signs") {
    MainTabs()
        .environment(ShieldStore.sample(named: "signs") ?? .preview())
}

#Preview("Call, ringing") {
    let call = LiveCall.ringing
    LiveCallView(call: call)
        .environment(ShieldStore.preview(call: call, isShowingCall: true))
}

#Preview("Call, suspicious") {
    let call = LiveCall.suspicious
    LiveCallView(call: call)
        .environment(ShieldStore.preview(call: call, isShowingCall: true))
}

#Preview("Palisade ended the call") {
    let call = LiveCall.endedByShield
    LiveCallView(call: call)
        .environment(ShieldStore.preview(call: call, isShowingCall: true))
}

#Preview("What happened") {
    NavigationStack {
        CallDetailView(call: .blocked)
    }
    .environment(ShieldStore.preview())
}

#Preview("Settings") {
    SettingsView()
        .environment(ShieldStore.preview())
}
#endif
