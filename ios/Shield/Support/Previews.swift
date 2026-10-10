#if DEBUG
import SwiftUI

// Xcode previews for every screen, built from SampleData. None of them touches the network.

#Preview("Home") {
    NavigationStack {
        HomeView()
    }
    .environment(ShieldStore.preview(history: CallSummary.samples))
}

#Preview("Home, no calls yet") {
    NavigationStack {
        HomeView()
    }
    .environment(ShieldStore.preview())
}

#Preview("Home, not connected") {
    NavigationStack {
        HomeView()
    }
    .environment(ShieldStore.preview(connection: .failed("Can’t reach the Shield server.")))
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

#Preview("Shield ended the call") {
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
