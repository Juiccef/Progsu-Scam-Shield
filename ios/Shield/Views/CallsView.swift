import SwiftUI

/// The "Calls" page: every call the server still keeps, newest first, or only the ones
/// Shield stepped into.
struct CallsView: View {
    @Environment(ShieldStore.self) private var store
    @AppStorage(SettingsKey.seniorName) private var seniorName = SettingsKey.defaultSeniorName
    @State private var filter = CallFilter.all

    var body: some View {
        let calls = filter.calls(from: store.history)
        List {
            Section {
                if !calls.isEmpty {
                    ForEach(calls) { call in
                        NavigationLink(value: call) {
                            CallRow(call: call)
                        }
                    }
                } else if !store.history.isEmpty {
                    // There are calls; none of them is one Shield stepped into.
                    ContentUnavailableView(
                        "No Flagged Calls",
                        systemImage: "hand.raised",
                        description: Text("Calls Shield warned about or ended show up here.")
                    )
                    .listRowBackground(Color.clear)
                } else {
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
        .animation(Motion.change, value: filter)
        .navigationTitle("Calls")
        .toolbar {
            // With no calls at all there is nothing to choose between.
            if !store.history.isEmpty {
                ToolbarItem(placement: .principal) {
                    Picker("Show", selection: $filter) {
                        ForEach(CallFilter.allCases) { filter in
                            Text(filter.title).tag(filter)
                        }
                    }
                    .pickerStyle(.segmented)
                }
            }
        }
        .refreshable {
            await store.refresh()
        }
    }
}

/// Which calls the Calls page lists.
private enum CallFilter: CaseIterable, Identifiable {
    case all
    /// The calls Shield warned about or ended.
    case flagged

    var id: Self { self }

    var title: String {
        switch self {
        case .all: "All"
        case .flagged: "Flagged"
        }
    }

    func calls(from history: [CallSummary]) -> [CallSummary] {
        switch self {
        case .all: history
        case .flagged: history.filter { $0.outcome == .warned || $0.outcome == .endedByShield }
        }
    }
}
