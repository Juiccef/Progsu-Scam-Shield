import SwiftUI

/// The app's pages behind the system's tab bar. On iOS 26 and later that bar is the system's
/// glass one; nothing here draws it.
struct MainTabs: View {
    @Environment(ShieldStore.self) private var store
    @AppStorage(SettingsKey.seniorName) private var seniorName = SettingsKey.defaultSeniorName

    var body: some View {
        // A call whose screen was put away stays one tap off, whichever page is showing.
        let hiddenCall = store.presentedCall == nil ? store.call.flatMap { $0.hasEnded ? nil : $0 } : nil
        tabs
            .tabBarAccessory(isShown: hiddenCall != nil) {
                if let hiddenCall {
                    LiveCallBar(call: hiddenCall, seniorName: seniorName)
                }
            }
    }

    @ViewBuilder private var tabs: some View {
        @Bindable var store = store
        if #available(iOS 18, macOS 15, *) {
            TabView(selection: $store.tab) {
                Tab(AppTab.home.title, systemImage: AppTab.home.symbol, value: AppTab.home) {
                    HomePage()
                }
                Tab(AppTab.calls.title, systemImage: AppTab.calls.symbol, value: AppTab.calls) {
                    CallsPage()
                }
                Tab(AppTab.texts.title, systemImage: AppTab.texts.symbol, value: AppTab.texts) {
                    TextsPage()
                }
                .badge(store.unreadGatewayIDs.count)
                Tab(AppTab.signs.title, systemImage: AppTab.signs.symbol, value: AppTab.signs) {
                    SignsPage()
                }
            }
        } else {
            TabView(selection: $store.tab) {
                HomePage()
                    .tabItem { Label(AppTab.home.title, systemImage: AppTab.home.symbol) }
                    .tag(AppTab.home)
                CallsPage()
                    .tabItem { Label(AppTab.calls.title, systemImage: AppTab.calls.symbol) }
                    .tag(AppTab.calls)
                TextsPage()
                    .tabItem { Label(AppTab.texts.title, systemImage: AppTab.texts.symbol) }
                    .badge(store.unreadGatewayIDs.count)
                    .tag(AppTab.texts)
                SignsPage()
                    .tabItem { Label(AppTab.signs.title, systemImage: AppTab.signs.symbol) }
                    .tag(AppTab.signs)
            }
        }
    }
}

extension AppTab {
    var title: String {
        switch self {
        case .home: "Summary"
        case .calls: "Calls"
        case .texts: "Texts"
        case .signs: "Scam Signs"
        }
    }

    /// The outline form. The tab bar fills in the selected one itself.
    var symbol: String {
        switch self {
        case .home: "checkmark.shield"
        case .calls: "phone"
        case .texts: "message"
        case .signs: "eye"
        }
    }
}

// Each page is its own navigation stack, so going into a call's summary on one page leaves
// the other pages where they were.

private struct HomePage: View {
    @Environment(ShieldStore.self) private var store

    var body: some View {
        @Bindable var store = store
        NavigationStack(path: $store.homePath) {
            HomeView()
                .navigationDestination(for: CallSummary.self) { call in
                    CallDetailView(call: call)
                }
        }
    }
}

private struct CallsPage: View {
    @Environment(ShieldStore.self) private var store

    var body: some View {
        @Bindable var store = store
        NavigationStack(path: $store.callsPath) {
            CallsView()
                .navigationDestination(for: CallSummary.self) { call in
                    CallDetailView(call: call)
                }
        }
    }
}

private struct TextsPage: View {
    @Environment(ShieldStore.self) private var store

    var body: some View {
        NavigationStack {
            MessagesView()
                .navigationDestination(isPresented: Binding(
                    get: { store.gatewayDetailID != nil },
                    set: { if !$0 { store.gatewayDetailID = nil } }
                )) {
                    if let id = store.gatewayDetailID { GatewayDetailView(messageID: id) }
                }
        }
    }
}

private struct SignsPage: View {
    var body: some View {
        NavigationStack {
            SignsView()
        }
    }
}

/// The way back to a call whose screen was put away: who is being called and how it is going.
private struct LiveCallBar: View {
    let call: LiveCall
    let seniorName: String

    @Environment(ShieldStore.self) private var store

    var body: some View {
        let status = call.statusTitle(warnAt: store.warnThreshold, endAt: store.endThreshold)
        Button {
            store.showCall()
        } label: {
            HStack(spacing: 8) {
                // The same red dot as "Live" in the call screen's header.
                Circle()
                    .fill(Theme.danger)
                    .frame(width: 8, height: 8)
                    .accessibilityHidden(true)
                // Who is being called always fits. How it is going is dropped when it does not
                // (large text): the call screen says it, one tap away.
                ViewThatFits(in: .horizontal) {
                    HStack(spacing: 8) {
                        title
                        Text(status)
                            .foregroundStyle(.secondary)
                    }
                    title
                }
                Spacer(minLength: 8)
                Image(systemName: "chevron.up")
                    .fontWeight(.semibold)
                    .foregroundStyle(.secondary)
                    .accessibilityHidden(true)
            }
            .font(.subheadline)
            .lineLimit(1)
            .padding(.horizontal, 18)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Call to \(seniorName), \(status)")
        .accessibilityHint("Opens the call")
    }

    private var title: some View {
        Text("Call to \(seniorName)")
            .fontWeight(.semibold)
    }
}
