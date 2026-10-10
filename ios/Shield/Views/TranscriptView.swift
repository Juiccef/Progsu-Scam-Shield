import SwiftUI

/// The conversation as it happens: the caller on the left, the protected person on the right.
struct TranscriptView: View {
    let lines: [TranscriptLine]
    let seniorName: String
    /// The highlighter color for the words Palisade flagged, once the call looks risky enough to mark them.
    let markColor: Color?

    private static let bottom = "bottom"

    var body: some View {
        if #available(iOS 18, macOS 15, *) {
            // The system keeps the newest line in view for as long as the reader is at the
            // bottom, and leaves the transcript alone once they scroll up to reread.
            scroll
                .defaultScrollAnchor(.bottom, for: .initialOffset)
                .defaultScrollAnchor(.bottom, for: .sizeChanges)
        } else {
            ScrollViewReader { proxy in
                scroll
                    .onChange(of: lines.last) {
                        withAnimation(Motion.quick) {
                            proxy.scrollTo(Self.bottom, anchor: .bottom)
                        }
                    }
                    .onAppear {
                        proxy.scrollTo(Self.bottom, anchor: .bottom)
                    }
            }
        }
    }

    private var scroll: some View {
        ScrollView {
            LazyVStack(spacing: 4) {
                ForEach(lines) { line in
                    TranscriptBubble(
                        line: line,
                        speakerName: line.speaker == .caller ? "Caller" : seniorName,
                        markColor: markColor
                    )
                }
                Color.clear
                    .frame(height: 1)
                    .id(Self.bottom)
            }
            .padding(.horizontal, 16)
            .padding(.bottom, 12)
        }
        .mask(alignment: .top) {
            VStack(spacing: 0) {
                LinearGradient(colors: [.clear, .black], startPoint: .top, endPoint: .bottom)
                    .frame(height: 14)
                Color.black
            }
        }
    }
}

struct TranscriptBubble: View {
    let line: TranscriptLine
    let speakerName: String
    let markColor: Color?

    var body: some View {
        let isCaller = line.speaker == .caller
        VStack(alignment: isCaller ? .leading : .trailing, spacing: 3) {
            if line.startsTurn {
                Text(speakerName)
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.secondary)
                    .padding(.horizontal, 6)
            }
            words
                .padding(.horizontal, 14)
                .padding(.vertical, 9)
                .background(isCaller ? Theme.surface : Theme.quietFill, in: .rect(cornerRadius: 18))
                .opacity(line.isFinal ? 1 : 0.55)
        }
        .frame(maxWidth: .infinity, alignment: isCaller ? .leading : .trailing)
        .padding(isCaller ? .trailing : .leading, 40)
        .padding(.top, line.startsTurn ? 10 : 0)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(speakerName): \(line.text)")
        .accessibilityValue(line.highlights.isEmpty ? "" : "Palisade flagged: \(line.highlights.formatted(.list(type: .and)))")
    }

    /// The line with the flagged phrases set in a heavier weight and, once the call looks
    /// suspicious, run over with a highlighter. The words themselves stay black.
    @ViewBuilder private var words: some View {
        let pieces = line.pieces
        if #available(iOS 18, macOS 15, *) {
            pieces.reduce(Text(verbatim: "")) { text, piece in
                let next = piece.isFlagged
                    ? Text(verbatim: piece.text).fontWeight(.semibold).customAttribute(Flagged())
                    : Text(verbatim: piece.text)
                return Text("\(text)\(next)")
            }
            .textRenderer(Highlighter(color: markColor ?? .clear, strength: markColor == nil ? 0 : 1))
            .animation(Motion.change, value: markColor != nil)
        } else {
            // Before iOS 18 text cannot be drawn over: the mark is a plain block behind the words.
            Text(pieces.reduce(into: AttributedString()) { text, piece in
                var part = AttributedString(piece.text)
                if piece.isFlagged {
                    part.font = .body.weight(.semibold)
                    if let markColor { part.backgroundColor = markColor.opacity(Highlight.opacity) }
                }
                text.append(part)
            })
        }
    }
}

private enum Highlight {
    static let opacity = 0.28
}

private extension TranscriptLine {
    struct Piece {
        let text: String
        let isFlagged: Bool
    }

    /// The line cut into the phrases Palisade flagged and the words between them, in order.
    var pieces: [Piece] {
        var flagged: [Range<String.Index>] = []
        for phrase in highlights where !phrase.isEmpty {
            var searchFrom = text.startIndex
            while let found = text.range(of: phrase, range: searchFrom..<text.endIndex) {
                flagged.append(found)
                searchFrom = found.upperBound
            }
        }
        flagged.sort { $0.lowerBound < $1.lowerBound }

        var pieces: [Piece] = []
        var cursor = text.startIndex
        for range in flagged where range.lowerBound >= cursor {
            if range.lowerBound > cursor {
                pieces.append(Piece(text: String(text[cursor..<range.lowerBound]), isFlagged: false))
            }
            pieces.append(Piece(text: String(text[range]), isFlagged: true))
            cursor = range.upperBound
        }
        if cursor < text.endIndex {
            pieces.append(Piece(text: String(text[cursor...]), isFlagged: false))
        }
        return pieces
    }
}

/// Marks a run of text as one of the phrases Palisade flagged.
@available(iOS 18, macOS 15, *)
private struct Flagged: TextAttribute {}

/// Draws a highlighter stroke with rounded ends behind each flagged phrase, one per line it
/// runs across, then the words on top.
@available(iOS 18, macOS 15, *)
private struct Highlighter: TextRenderer {
    var color: Color
    /// 0 while flagged phrases are only heavier, 1 once they are marked. Animates between.
    var strength: Double

    var animatableData: Double {
        get { strength }
        set { strength = newValue }
    }

    /// The stroke reaches a little past the words at each end.
    var displayPadding: EdgeInsets {
        EdgeInsets(top: 0, leading: 3, bottom: 0, trailing: 3)
    }

    func draw(layout: Text.Layout, in context: inout GraphicsContext) {
        if strength > 0 {
            for line in layout {
                // A phrase is one stroke per line, however many runs the system cut it into.
                var strokes: [CGRect] = []
                var isContinuing = false
                for run in line {
                    guard run[Flagged.self] != nil else {
                        isContinuing = false
                        continue
                    }
                    let rect = run.typographicBounds.rect
                    if isContinuing, let last = strokes.popLast() {
                        strokes.append(last.union(rect))
                    } else {
                        strokes.append(rect)
                    }
                    isContinuing = true
                }
                for stroke in strokes {
                    let band = stroke.insetBy(dx: -3, dy: 1.5)
                    context.fill(
                        Path(roundedRect: band, cornerRadius: 5),
                        with: .color(color.opacity(Highlight.opacity * strength))
                    )
                }
            }
        }
        for line in layout {
            context.draw(line)
        }
    }
}
