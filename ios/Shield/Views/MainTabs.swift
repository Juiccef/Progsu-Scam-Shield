import SwiftUI

/// The app's pages behind the system's tab bar. On iOS 26 and later that bar is the system's
/// glass one; nothing here draws it.
struct MainTabs: View {
    @Environment(ShieldStore.self) private var store

    var body: some View {
        @Bindable var store = store
        if #available(iOS 18, macOS 15, *) {
            TabView(selection: $store.tab) {
                Tab(AppTab.home.title, systemImage: AppTab.home.symbol, value: AppTab.home) {
                    HomePage()
                }
                Tab(AppTab.calls.title, systemImage: AppTab.calls.symbol, value: AppTab.calls) {
                    CallsPage()
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
            }
        }
    }
}

extension AppTab {
    var title: String {
        switch self {
        case .home: "Summary"
        case .calls: "Calls"
        }
    }

    /// The outline form. The tab bar fills in the selected one itself.
    var symbol: String {
        switch self {
        case .home: "checkmark.shield"
        case .calls: "phone"
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
