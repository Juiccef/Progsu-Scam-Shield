import SwiftUI

/// Home, with the call screen sliding over it whenever a call is in progress.
///
/// The call screen is an overlay rather than a modal so that it can always appear at once,
/// even if a sheet is on screen when the phone rings.
struct RootView: View {
    @State private var store: ShieldStore
    @AppStorage(SettingsKey.appearance) private var appearance = Appearance.automatic
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
                    .transition(.move(edge: .bottom))
                    .zIndex(1)
            }
        }
        .animation(.snappy(duration: 0.4), value: store.presentedCall?.id)
        .alert("Couldn’t start the demo call", isPresented: $store.isShowingDemoError) {
            Button("OK") {}
        } message: {
            Text(store.demoError ?? "")
        }
        .environment(store)
        .tint(Theme.safe)
        .onChange(of: appearance, initial: true) { old, new in
            // At launch the two are the same: set it at once, with no fade from the wrong appearance.
            new.apply(animated: old != new)
        }
        .task {
            if connectsToServer { store.connect() }
        }
    }
}
