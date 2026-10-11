import SwiftUI

struct SettingsView: View {
    @Environment(ShieldStore.self) private var store
    @Environment(\.dismiss) private var dismiss
    @AppStorage(SettingsKey.serverURL) private var serverURL = SettingsKey.defaultServerURL
    /// Not `@AppStorage`: the token is kept in the Keychain, and written there as it is typed.
    @State private var accessToken = AccessToken.current
    @AppStorage(SettingsKey.seniorName) private var seniorName = SettingsKey.defaultSeniorName
    @AppStorage(SettingsKey.seniorPhone) private var seniorPhone = ""
    @AppStorage(SettingsKey.appearance) private var appearance = Appearance.automatic

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    NamedField(name: "Address") {
                        TextField("Address", text: $serverURL, prompt: Text("palisade.example.com"))
                            .literalEntry()
                    }
                    // A server that asks each caregiver to sign in has no use for a shared token.
                    if !store.offersSignIn {
                        NamedField(name: "Access Token") {
                            SecureField("Access Token", text: $accessToken, prompt: Text("Only if your server has one"))
                        }
                    }
                    ConnectionRow(connection: store.connection)
                } header: {
                    Text("Palisade Server")
                } footer: {
                    Text("The web address of your Palisade server.")
                }

                if store.offersSignIn {
                    Section {
                        if let account = store.account {
                            // An email address broken over two lines gets a hyphen it does not
                            // have: keep it on one, smaller before it is cut short.
                            Text(account)
                                .lineLimit(1)
                                .minimumScaleFactor(0.5)
                                .truncationMode(.middle)
                            Button("Sign Out") {
                                Task { await store.signOut() }
                            }
                            .disabled(store.isSigningInOrOut)
                        } else {
                            SignInButton()
                        }
                    } header: {
                        Text("Account")
                    } footer: {
                        Text("Your Palisade server shows calls only to the people on its caregiver list.")
                    }
                }

                Section {
                    NamedField(name: "Name") {
                        TextField("Name", text: $seniorName, prompt: Text("Mom"))
                    }
                    NamedField(name: "Phone Number") {
                        TextField("Phone Number", text: $seniorPhone, prompt: Text("Optional"))
                            .phoneEntry()
                    }
                } header: {
                    Text("Who Palisade Protects")
                } footer: {
                    Text("The number stays on this phone. It is only used for the Call button that appears when Palisade warns you about a call or ends one.")
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
                    // The server's own pages, which need no sign-in. They open in the browser.
                    // A server from before those pages gets no links, not two that lead nowhere.
                    if let pages = store.publicPages {
                        Link("Privacy Policy", destination: pages.appending(path: "privacy"))
                        Link("Support", destination: pages.appending(path: "support"))
                    }
                    LabeledContent("Version", value: Bundle.main.versionText)
                } footer: {
                    Label("No audio or transcript of a call is stored. A text sent to the protected number is kept encrypted for up to 24 hours, so it can be reviewed and delivered, then deleted.", systemImage: "lock.fill")
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
        .onChange(of: accessToken) { _, token in
            AccessToken.stored = token
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

private extension Bundle {
    /// The app's version and build number, written the way the App Store writes them: "1.0 (1)".
    var versionText: String {
        let version = infoDictionary?["CFBundleShortVersionString"] as? String ?? ""
        let build = infoDictionary?["CFBundleVersion"] as? String ?? ""
        return build.isEmpty ? version : "\(version) (\(build))"
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
        case .notConfigured, .signedOut, .refused, .failed: Theme.caution
        }
    }

    private var text: String {
        switch connection {
        case .connected: "Connected"
        case .connecting: "Connecting"
        case .notConfigured: "No Address"
        case .signedOut: "Not Signed In"
        case .refused: "No Access"
        case .failed: "Can’t Connect"
        }
    }
}
