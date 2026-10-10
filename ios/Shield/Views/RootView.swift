import SwiftUI

/// Home, with the call screen sliding over it whenever a call is in progress.
///
/// The call screen is an overlay rather than a modal so that it can always appear at once,
/// even if a sheet is on screen when the phone rings.
struct RootView: View {
    @State private var store: ShieldStore
    @AppStorage(SettingsKey.appearance) private var appearance = Appearance.automatic
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    /// False when the store holds fixed sample data, which a live feed must not replace.
    private let connectsToServer: Bool

    init() {
        #if DEBUG
        // `-sample suspicious` as a launch argument shows that screen with no server.
        if let name = UserDefaults.standard.string(forKey: "sample"), let sample = ShieldStore.sample(named: name) {
            _store = State(initialValue: sample)
            connectsToServer = false
            return
        }
        #endif
        _store = State(initialValue: ShieldStore())
        connectsToServer = true
    }

    var body: some View {
        ZStack {
            NavigationStack(path: $store.path) {
                HomeView()
                    .navigationDestination(for: CallSummary.self) { call in
                        CallDetailView(call: call)
                    }
            }
            if let call = store.presentedCall {
                LiveCallView(call: call)
                    // Whatever turns up on the screen while it is still sliding in rides along
                    // with it, instead of appearing where the screen is going to stop.
                    .geometryGroup()
                    .transition(reduceMotion ? .opacity : .move(edge: .bottom))
                    .zIndex(1)
            }
        }
        .animation(Motion.screen, value: store.presentedCall?.id)
        .alert("Couldn’t start the demo call", isPresented: $store.isShowingDemoError) {
            Button("OK") {}
        } message: {
            Text(store.demoError ?? "")
        }
        .environment(store)
        // Here rather than on the call screen, so the buzz comes whichever screen is showing.
        .sensoryFeedback(trigger: store.latestAction) { _, action in
            switch action?.kind {
            case .warn: .warning
            case .end: .success
            case nil: nil
            }
        }
        .onChange(of: appearance, initial: true) { old, new in
            // At launch the two are the same: set it at once, with no fade from the wrong appearance.
            new.apply(animated: old != new)
        }
        .task {
            if connectsToServer { store.connect() }
        }
    }
}
