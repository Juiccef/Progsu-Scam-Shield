import SwiftUI

/// Home, with the call screen sliding over it whenever a call is in progress.
///
/// The call screen is an overlay rather than a modal so that it can always appear at once,
/// even if a sheet is on screen when the phone rings.
struct RootView: View {
    @State private var store: ShieldStore
    @AppStorage(SettingsKey.appearance) private var appearance = Appearance.automatic
    @Environment(\.scenePhase) private var scenePhase
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
        @Bindable var store = store
        ZStack {
            TabView(selection: $store.selectedTab) {
                NavigationStack(path: $store.path) {
                    HomeView()
                        .navigationDestination(for: CallSummary.self) { call in
                            CallDetailView(call: call)
                        }
                }
                .tabItem { Label("Home", systemImage: "shield") }
                .tag(ShieldStore.Tab.home)
                NavigationStack {
                    MessagesView()
                        .navigationDestination(isPresented: Binding(
                            get: { store.gatewayDetailID != nil },
                            set: { if !$0 { store.gatewayDetailID = nil } }
                        )) {
                            if let id = store.gatewayDetailID { GatewayDetailView(messageID: id) }
                        }
                }
                .tabItem { Label("Texts", systemImage: "message") }
                .badge(store.unreadGatewayIDs.count)
                .tag(ShieldStore.Tab.texts)
            }
            .overlay(alignment: .top) {
                if let id = store.gatewayBannerID, store.presentedCall == nil {
                    HStack {
                        Button {
                            store.selectedTab = .texts
                            store.gatewayDetailID = id
                            store.gatewayBannerID = nil
                        } label: {
                            Label("Shield held a text. Review it.", systemImage: "exclamationmark.bubble")
                                .font(.subheadline.weight(.semibold))
                        }
                        Spacer()
                        Button("Dismiss", systemImage: "xmark") { store.gatewayBannerID = nil }
                            .labelStyle(.iconOnly)
                    }
                    .padding(16)
                    .background(Theme.surface, in: .rect(cornerRadius: 16))
                    .shadow(radius: 4)
                    .padding()
                    .zIndex(2)
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
        .onChange(of: store.selectedTab) { _, tab in
            if tab == .texts { store.markGatewayRead() }
        }
        .onChange(of: scenePhase) { _, phase in
            if phase == .active, connectsToServer { Task { await store.refresh() } }
        }
        .task {
            if connectsToServer { store.connect() }
            #if DEBUG
            // Deterministic simulator entrypoints for the protected inbox/review.
            if UserDefaults.standard.string(forKey: "tab") == "texts" {
                store.selectedTab = .texts
            }
            if let id = UserDefaults.standard.string(forKey: "gatewayMessageID") {
                store.selectedTab = .texts
                store.gatewayDetailID = id
            }
            #endif
        }
    }
}
