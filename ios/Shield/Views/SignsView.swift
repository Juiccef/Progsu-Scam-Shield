import SwiftUI

/// The "Scam Signs" page: what Palisade does about a risky call, and everything it listens for.
struct SignsView: View {
    @Environment(ShieldStore.self) private var store
    @AppStorage(SettingsKey.seniorName) private var seniorName = SettingsKey.defaultSeniorName

    var body: some View {
        List {
            Section {
                DetailRow(
                    title: "Listens",
                    detail: "Palisade follows each call to \(seniorName)’s phone as it happens and scores how risky it looks, from 0 to 100."
                )
                // The two numbers are the server's own, so this page cannot drift from what it does.
                DetailRow(
                    title: "Warns You",
                    detail: "At a risk of \(store.warnThreshold). The call screen opens and says why."
                )
                DetailRow(
                    title: "Ends the Call",
                    detail: "At a risk of \(store.endThreshold). \(seniorName) hears a short safety message, then the caller is disconnected."
                )
            } header: {
                Text("How Palisade Responds")
            } footer: {
                if store.isPracticeMode {
                    Text("Practice mode is on for this server: Palisade shows what it would do, but does not end real calls.")
                }
            }
            ForEach(Flag.groups) { group in
                Section(group.title) {
                    ForEach(group.flags) { flag in
                        FlagRow(flag: flag)
                    }
                }
            }
            Section {
            } footer: {
                Label("No audio or transcript is stored. Palisade keeps what it detected, not what was said.", systemImage: "lock.fill")
            }
        }
        .groupedListStyle()
        .headerProminence(.increased)
        .navigationTitle("Scam Signs")
    }
}
