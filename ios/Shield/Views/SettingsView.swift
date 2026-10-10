import SwiftUI

struct SettingsView: View {
    @Environment(ShieldStore.self) private var store
    @Environment(\.dismiss) private var dismiss
    @AppStorage(SettingsKey.serverURL) private var serverURL = SettingsKey.defaultServerURL
    @AppStorage(SettingsKey.accessToken) private var accessToken = ""
    @AppStorage(SettingsKey.seniorName) private var seniorName = SettingsKey.defaultSeniorName
    @AppStorage(SettingsKey.seniorPhone) private var seniorPhone = ""
    @AppStorage(SettingsKey.appearance) private var appearance = Appearance.automatic

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    NamedField(name: "Address") {
                        TextField("Address", text: $serverURL, prompt: Text("shield.example.com"))
                            .literalEntry()
                    }
                    NamedField(name: "Access token") {
                        SecureField("Access token", text: $accessToken, prompt: Text("Only if your server has one"))
                    }
                    ConnectionRow(connection: store.connection)
                } header: {
                    Text("Shield server")
                } footer: {
                    Text("The address of your Shield server, for example the ngrok address it is running behind.")
                }

                Section {
                    NamedField(name: "Name") {
                        TextField("Name", text: $seniorName, prompt: Text("Mom"))
                    }
                    NamedField(name: "Phone number") {
                        TextField("Phone number", text: $seniorPhone, prompt: Text("Optional"))
                            .phoneEntry()
                    }
                } header: {
                    Text("Who Shield protects")
                } footer: {
                    Text("The number stays on this phone. It is only used for the Call button that appears when Shield warns you about a call or ends one.")
                }

                Section {
                    Picker("Appearance", selection: $appearance) {
                        ForEach(Appearance.allCases) { option in
                            Text(option.title).tag(option)
                        }
                    }
                    .pickerStyle(.segmented)
                } header: {
                    Text("Appearance")
                } footer: {
                    Text("Automatic follows this phone’s Light or Dark setting.")
                }

                Section {
                    Label("No audio or transcript is stored. Shield keeps what it detected, not what was said.", systemImage: "lock.fill")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
            }
            .formStyle(.grouped)
            .navigationTitle("Settings")
            .inlineNavigationTitle()
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
        }
        .onChange(of: store.presentedCall?.id) {
            // A call just started: get out of its way.
            if store.presentedCall != nil { dismiss() }
        }
        .onDisappear {
            store.connectIfSettingsChanged()
        }
    }
}

/// A field with its name above it. On iPhone a form's text field shows only its prompt,
/// which is gone as soon as something is typed.
private struct NamedField<Field: View>: View {
    let name: String
    @ViewBuilder let field: Field

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(name)
                .font(.caption)
                .foregroundStyle(.secondary)
                .accessibilityHidden(true)  // the field carries the same label
            field
        }
    }
}

private struct ConnectionRow: View {
    let connection: ShieldStore.Connection

    var body: some View {
        LabeledContent("Status") {
            HStack(spacing: 6) {
                Circle()
                    .fill(tint)
                    .frame(width: 8, height: 8)
                    .accessibilityHidden(true)
                Text(text)
            }
        }
    }

    private var tint: Color {
        switch connection {
        case .connected: Theme.safe
        case .connecting: Theme.watchful
        case .notConfigured, .failed: Theme.caution
        }
    }

    private var text: String {
        switch connection {
        case .connected: "Connected"
        case .connecting: "Connecting"
        case .notConfigured: "No address"
        case .failed: "Can’t connect"
        }
    }
}
