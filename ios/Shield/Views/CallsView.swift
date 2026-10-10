import SwiftUI

/// The "Calls" page: every call the server still keeps, newest first.
struct CallsView: View {
    @Environment(ShieldStore.self) private var store
    @AppStorage(SettingsKey.seniorName) private var seniorName = SettingsKey.defaultSeniorName

    var body: some View {
        List {
            if !store.history.isEmpty {
                Section {
                    ForEach(store.history) { call in
                        NavigationLink(value: call) {
                            CallRow(call: call)
                        }
                    }
                }
            } else {
                Section {
                    switch store.connection {
                    case .connected:
                        NoCallsYet(seniorName: seniorName)
                    case .notConfigured, .failed:
                        // Why the page is empty. The way to fix it is on the Summary page.
                        ContentUnavailableView(
                            "Not Connected",
                            systemImage: "exclamationmark.shield",
                            description: Text("Calls show up here once Shield reaches its server.")
                        )
                        .listRowBackground(Color.clear)
                    case .connecting:
                        // Not known to be empty yet: say nothing for the moment it takes.
                        EmptyView()
                    }
                }
            }
        }
        .groupedListStyle()
        .navigationTitle("Calls")
        .refreshable {
            await store.refresh()
        }
    }
}
