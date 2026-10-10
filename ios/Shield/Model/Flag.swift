import Foundation

/// A scam warning sign the server detected. IDs match the server's flag list.
struct Flag: Identifiable, Hashable, Sendable {
    let id: String

    var title: String { Self.catalogue[id]?.title ?? id.replacingOccurrences(of: "_", with: " ").capitalized }
    var detail: String { Self.catalogue[id]?.detail ?? "Shield flagged this as a warning sign." }
    /// Lower is more telling. Payment and secrecy demands outrank a claimed job title.
    var rank: Int { Self.order.firstIndex(of: id) ?? Self.order.count }

    private static let order = [
        "UNUSUAL_PAYMENT", "SAFE_ACCOUNT_TRANSFER", "REMOTE_ACCESS", "OTP_REQUEST", "CREDENTIAL_REQUEST", "SECRECY",
        "FAMILY_EMERGENCY", "PRIZE_OR_REFUND", "VERIFICATION_AVOIDANCE", "THREAT", "STAY_ON_LINE", "URGENCY_PRESSURE",
        "AUTHORITY_IMPERSONATION",
    ]

    private struct Entry {
        let title: String
        let detail: String
    }

    private static let catalogue: [String: Entry] = [
        "UNUSUAL_PAYMENT": Entry(
            title: "Gift cards, wire or crypto",
            detail: "The caller asked to be paid in a way that can't be traced or reversed."
        ),
        "SAFE_ACCOUNT_TRANSFER": Entry(
            title: "“Safe account” transfer",
            detail: "The caller asked to move money into an account they control."
        ),
        "REMOTE_ACCESS": Entry(
            title: "Remote access",
            detail: "The caller asked to install software or take control of a device."
        ),
        "OTP_REQUEST": Entry(
            title: "Verification code",
            detail: "The caller asked for a one-time code that was sent by text."
        ),
        "CREDENTIAL_REQUEST": Entry(
            title: "SSN, PIN or password",
            detail: "The caller asked for a full account number, PIN, password or Social Security number."
        ),
        "SECRECY": Entry(
            title: "Demands secrecy",
            detail: "The caller said not to tell family or the bank."
        ),
        "FAMILY_EMERGENCY": Entry(
            title: "Relative in trouble",
            detail: "The caller claimed a family member urgently needs money."
        ),
        "PRIZE_OR_REFUND": Entry(
            title: "Prize or refund bait",
            detail: "The caller promised money in exchange for a fee."
        ),
        "VERIFICATION_AVOIDANCE": Entry(
            title: "Discourages checking",
            detail: "The caller argued against hanging up to verify the story."
        ),
        "THREAT": Entry(
            title: "Threats",
            detail: "The caller threatened arrest, a shutoff or legal action."
        ),
        "STAY_ON_LINE": Entry(
            title: "Won’t let them hang up",
            detail: "The caller insisted on staying on the line."
        ),
        "URGENCY_PRESSURE": Entry(
            title: "Time pressure",
            detail: "The caller demanded action right away."
        ),
        "AUTHORITY_IMPERSONATION": Entry(
            title: "Claims to be an official",
            detail: "The caller said they were from the government, a bank or a company."
        ),
    ]
}
