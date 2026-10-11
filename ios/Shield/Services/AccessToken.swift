import Foundation
import Security

/// The Access Token from Settings: the one secret the caregiver types, for a server that asks
/// for no sign-in. It is kept in the Keychain. The app's other settings are stored as plain
/// text, which is no place for something that opens the feed of someone's calls.
enum AccessToken {
    /// The token to send. A launch argument (`-accessToken demo-caregiver`) stands in for
    /// the typed one for that launch, as `-serverURL` does for the address.
    static var current: String {
        let arguments = UserDefaults.standard.volatileDomain(forName: UserDefaults.argumentDomain)
        return arguments[SettingsKey.accessToken] as? String ?? stored
    }

    /// The token as typed in Settings. Setting it to nothing removes it from the Keychain.
    static var stored: String {
        get {
            if let token = read() { return token }
            return moveFromSettings() ?? ""
        }
        set {
            write(newValue)
        }
    }

    private static var item: [String: Any] {
        [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: Bundle.main.bundleIdentifier ?? "Palisade",
            kSecAttrAccount as String: SettingsKey.accessToken,
        ]
    }

    private static func read() -> String? {
        var query = item
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var found: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &found) == errSecSuccess, let data = found as? Data else {
            return nil
        }
        return String(data: data, encoding: .utf8)
    }

    private static func write(_ token: String) {
        guard !token.isEmpty else {
            SecItemDelete(item as CFDictionary)
            return
        }
        let data = Data(token.utf8)
        let status = SecItemUpdate(item as CFDictionary, [kSecValueData as String: data] as CFDictionary)
        guard status == errSecItemNotFound else { return }
        var new = item
        new[kSecValueData as String] = data
        // Readable while the phone is unlocked, which is the only time the app runs, and not
        // carried to another phone by a backup: there it is typed again.
        new[kSecAttrAccessible as String] = kSecAttrAccessibleWhenUnlockedThisDeviceOnly
        SecItemAdd(new as CFDictionary, nil)
    }

    /// Earlier builds kept the token with the app's settings. The first time it is asked for,
    /// it is moved to the Keychain and removed from there.
    private static func moveFromSettings() -> String? {
        let defaults = UserDefaults.standard
        guard let domain = Bundle.main.bundleIdentifier,
              let token = defaults.persistentDomain(forName: domain)?[SettingsKey.accessToken] as? String
        else { return nil }
        defaults.removeObject(forKey: SettingsKey.accessToken)
        guard !token.isEmpty else { return nil }
        write(token)
        return token
    }
}
