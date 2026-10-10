import Auth0
import Foundation

/// What `GET /auth/config` on the server answers. The server says whether it wants a sign-in
/// and which Auth0 tenant to use, so the app keeps no Auth0 settings of its own.
struct SignInConfig: Decodable, Equatable, Sendable {
    var enabled: Bool
    var domain: String?
    var audience: String?
    var iosClientID: String?

    enum CodingKeys: String, CodingKey {
        case enabled, domain, audience
        case iosClientID = "ios_client_id"
    }

    /// A server that asks for no sign-in.
    static let off = SignInConfig(enabled: false)
}

/// A sign-in that did not go through, in words the caregiver can be shown.
struct SignInProblem: LocalizedError {
    let errorDescription: String?
}

/// One caregiver's session with Auth0: signing in and out, and the token the server accepts.
/// Credentials are kept in the Keychain by Auth0's credentials manager, not in app settings.
///
/// Keep one of these for as long as the server's answer stays the same: Auth0's library can
/// only keep two renewals of the same token apart when both go through one credentials manager.
final class SignIn: Sendable {
    let config: SignInConfig
    private let clientID: String
    private let domain: String
    private let audience: String
    private let manager: CredentialsManager

    /// Nil when the server asks for no sign-in, or has not been told the app's client ID.
    init?(_ config: SignInConfig) {
        guard config.enabled, let domain = config.domain, let audience = config.audience,
              let clientID = config.iosClientID, !domain.isEmpty, !audience.isEmpty, !clientID.isEmpty
        else { return nil }
        self.config = config
        self.clientID = clientID
        self.domain = domain
        self.audience = audience
        manager = CredentialsManager(
            authentication: Auth0.authentication(clientId: clientID, domain: domain),
            // Kept apart for each Auth0 tenant and application, so a sign-in made for one
            // server is never offered to another.
            storeKey: "caregiver \(domain) \(clientID)"
        )
    }

    /// Who is signed in on this phone: their email. Nil when nobody is.
    var account: String? {
        guard manager.hasValid() || manager.canRenew() else { return nil }
        let profile = try? manager.userProfile()
        return profile?.email ?? profile?.name ?? "Signed In"
    }

    /// Opens Auth0's sign-in page in the system's sign-in sheet. Throws `CancellationError`
    /// when the caregiver closes the sheet, and a `SignInProblem` when Auth0 says no.
    func signIn() async throws {
        do {
            _ = try await Auth0
                .webAuth(clientId: clientID, domain: domain)
                .audience(audience)
                // `offline_access` asks for the token that keeps the caregiver signed in.
                .scope("openid profile email offline_access")
                // A private browser session, which leaves nothing behind in Safari. Without it
                // iOS first asks whether "Shield" may use Auth0's address to sign in, under the
                // target's name and with no way to reword it, and asks again on signing out.
                .useEphemeralSession()
                .useCredentialsManager(manager)
                .start()
        } catch WebAuthError.userCancelled {
            throw CancellationError()
        } catch {
            // Auth0's own sentence, where it gave one: it names what is set up wrong.
            if let refusal = (error as? WebAuthError)?.cause as? AuthenticationError, !refusal.isNetworkError {
                throw SignInProblem(errorDescription: "Auth0 answered: \(refusal.localizedDescription)")
            }
            throw SignInProblem(errorDescription: "Auth0’s sign-in page could not be reached. Try again.")
        }
    }

    /// Makes this phone forget the caregiver. The token that kept them signed in is also
    /// withdrawn at Auth0, when Auth0 can be reached. Signing in left nothing in a browser,
    /// so there is nothing else to clear.
    func signOut() async {
        try? await manager.revoke()
        try? manager.clear()
    }

    /// A token the server accepts, renewed when it has run out. Nil when nobody is signed in,
    /// or the sign-in has ended and has to be done again. Throws when a sign-in is there but
    /// Auth0 could not be reached to renew its token, which is worth another try.
    func accessToken() async throws -> String? {
        do {
            return try await manager.credentials().accessToken
        } catch let error as CredentialsManagerError {
            if case CredentialsManagerError.noCredentials = error { return nil }
            if case CredentialsManagerError.renewFailed = error,
               let cause = error.cause as? AuthenticationError, cause.isRetryable {
                throw error
            }
            // The sign-in is over (its token ran out, or was withdrawn): do not go on showing it.
            try? manager.clear()
            return nil
        }
    }
}
