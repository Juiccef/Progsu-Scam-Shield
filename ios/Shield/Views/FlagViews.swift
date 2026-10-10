import SwiftUI

/// A warning sign with its explanation, for the summary of a call.
struct FlagRow: View {
    let flag: Flag

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(flag.title)
            Text(flag.detail)
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .accessibilityElement(children: .combine)
    }
}

/// A row whose children all get the same width: the widest child's, or more if there is room.
/// Its ideal width is honest about that, so `ViewThatFits` can tell when the row would truncate.
struct EqualWidthHStack: Layout {
    var spacing: CGFloat = 8

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let ideal = idealSize(of: subviews)
        guard let width = proposal.width, width.isFinite else { return ideal }
        return CGSize(width: max(width, ideal.width), height: ideal.height)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        guard !subviews.isEmpty else { return }
        let width = (bounds.width - spacing * CGFloat(subviews.count - 1)) / CGFloat(subviews.count)
        var x = bounds.minX
        for subview in subviews {
            subview.place(at: CGPoint(x: x, y: bounds.minY), proposal: ProposedViewSize(width: width, height: bounds.height))
            x += width + spacing
        }
    }

    private func idealSize(of subviews: Subviews) -> CGSize {
        let sizes = subviews.map { $0.sizeThatFits(.unspecified) }
        let widest = sizes.map(\.width).max() ?? 0
        let tallest = sizes.map(\.height).max() ?? 0
        return CGSize(
            width: widest * CGFloat(subviews.count) + spacing * CGFloat(max(subviews.count - 1, 0)),
            height: tallest
        )
    }
}
