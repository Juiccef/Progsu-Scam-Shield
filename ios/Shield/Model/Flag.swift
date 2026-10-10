import Foundation

/// A scam warning sign the server detected. IDs match the server's flag list.
struct Flag: Identifiable, Hashable, Sendable {
    let id: String

    var title: String { Self.catalogue[id]?.title ?? id.replacingOccurrences(of: "_", with: " ").capitalized }
    var detail: String { Self.catalogue[id]?.detail ?? "Palisade flagged this as a warning sign." }
    /// Lower is more telling. Payment and secrecy demands outrank a claimed job title.
    var rank: Int { Self.order.firstIndex(of: id) ?? Self.order.count }

    private static let order = [
        "UNUSUAL_PAYMENT", "SAFE_ACCOUNT_TRANSFER", "REMOTE_ACCESS", "OTP_REQUEST", "CREDENTIAL_REQUEST", "SECRECY",
        "FAMILY_EMERGENCY", "PRIZE_OR_REFUND", "VERIFICATION_AVOIDANCE", "THREAT", "STAY_ON_LINE", "URGENCY_PRESSURE",
        "AUTHORITY_IMPERSONATION",
    ]

    /// Some of the signs under one heading, for the page that lists them all.
    struct Group: Identifiable {
        let title: String
        let flags: [Flag]

        var id: String { title }
    }

    /// Every sign Palisade listens for, in the order a scam call tends to go: who the caller
    /// says they are, how they push, and what they are after.
    static let groups = [
        Group(title: "Who They Claim to Be", flags: ["AUTHORITY_IMPERSONATION", "FAMILY_EMERGENCY"].map(Flag.init(id:))),
        Group(
            title: "How They Pressure",
            flags: ["THREAT", "URGENCY_PRESSURE", "STAY_ON_LINE", "SECRECY", "VERIFICATION_AVOIDANCE"].map(Flag.init(id:))
        ),
        Group(
            title: "What They Ask For",
            flags: [
                "UNUSUAL_PAYMENT", "SAFE_ACCOUNT_TRANSFER", "PRIZE_OR_REFUND", "REMOTE_ACCESS", "OTP_REQUEST",
                "CREDENTIAL_REQUEST",
            ].map(Flag.init(id:))
        ),
    ]

    private struct Entry {
        let title: String
        let detail: String
    }

    private static let catalogue: [String: Entry] = [
        "UNUSUAL_PAYMENT": Entry(
            title: "Gift Cards, Wire or Crypto",
            detail: "The caller asked to be paid in a way that can't be traced or reversed."
        ),
        "SAFE_ACCOUNT_TRANSFER": Entry(
            title: "“Safe Account” Transfer",
            detail: "The caller asked to move money into an account they control."
        ),
        "REMOTE_ACCESS": Entry(
            title: "Remote Access",
            detail: "The caller asked to install software or take control of a device."
        ),
        "OTP_REQUEST": Entry(
            title: "Verification Code",
            detail: "The caller asked for a one-time code that was sent by text."
        ),
        "CREDENTIAL_REQUEST": Entry(
            title: "SSN, PIN or Password",
            detail: "The caller asked for a full account number, PIN, password or Social Security number."
        ),
        "SECRECY": Entry(
            title: "Demands Secrecy",
            detail: "The caller said not to tell family or the bank."
        ),
        "FAMILY_EMERGENCY": Entry(
            title: "Relative in Trouble",
            detail: "The caller claimed a family member urgently needs money."
        ),
        "PRIZE_OR_REFUND": Entry(
            title: "Prize or Refund Bait",
            detail: "The caller promised money in exchange for a fee."
        ),
        "VERIFICATION_AVOIDANCE": Entry(
            title: "Discourages Checking",
            detail: "The caller argued against hanging up to verify the story."
        ),
        "THREAT": Entry(
            title: "Threats",
            detail: "The caller threatened arrest, a shutoff or legal action."
        ),
        "STAY_ON_LINE": Entry(
            title: "Won’t Let Them Hang Up",
            detail: "The caller insisted on staying on the line."
        ),
        "URGENCY_PRESSURE": Entry(
            title: "Time Pressure",
            detail: "The caller demanded action right away."
        ),
        "AUTHORITY_IMPERSONATION": Entry(
            title: "Claims to Be an Official",
            detail: "The caller said they were from the government, a bank or a company."
        ),
    ]
}
