import SwiftUI

/// "Sign In", wherever it is offered. It opens Auth0's page in the system's sign-in sheet, and
/// says so when that goes wrong. Closing the sheet is not something going wrong.
struct SignInButton: View {
    /// True on Summary's card, where it is the card's capsule. In a form it is an ordinary row.
    var isOnCard = false

    @Environment(ShieldStore.self) private var store
    @State private var problem: String?

    var body: some View {
        button
            // Outside the card's tint, so the alert's own button is drawn as the system draws it.
            .alert("Couldn’t Sign In", isPresented: isShowingProblem) {
                Button("OK") {}
            } message: {
                Text(problem ?? "")
            }
    }

    @ViewBuilder private var button: some View {
        let button = Button("Sign In") {
            Task { problem = await store.signIn() }
        }
        .disabled(store.isSigningInOrOut)
        if isOnCard {
            button.cardButton()
        } else {
            button
        }
    }

    private var isShowingProblem: Binding<Bool> {
        Binding {
            problem != nil
        } set: { isShown in
            if !isShown { problem = nil }
        }
    }
}
