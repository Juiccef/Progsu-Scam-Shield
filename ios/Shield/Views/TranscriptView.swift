import SwiftUI

/// The conversation as it happens: the caller on the left, the protected person on the right.
struct TranscriptView: View {
    let lines: [TranscriptLine]
    let seniorName: String
    /// The highlighter color for the words Shield flagged, once the call looks risky enough to mark them.
    let markColor: Color?

    private static let bottom = "bottom"

    var body: some View {
        ScrollViewReader { proxy in
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
            .onChange(of: lines.last) {
                withAnimation(.easeOut(duration: 0.2)) {
                    proxy.scrollTo(Self.bottom, anchor: .bottom)
                }
            }
            .onAppear {
                proxy.scrollTo(Self.bottom, anchor: .bottom)
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
            Text(markedText)
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
        .accessibilityValue(line.highlights.isEmpty ? "" : "Shield flagged: \(line.highlights.formatted(.list(type: .and)))")
    }

    /// The line with the flagged phrases set in a heavier weight and, once the call looks
    /// suspicious, run over with a highlighter. The words themselves stay black.
    private var markedText: AttributedString {
        var text = AttributedString(line.text)
        for phrase in line.highlights where !phrase.isEmpty {
            var searchFrom = text.startIndex
            while let range = text[searchFrom...].range(of: phrase) {
                text[range].font = .body.weight(.semibold)
                if let markColor { text[range].backgroundColor = markColor.opacity(0.28) }
                searchFrom = range.upperBound
            }
        }
        return text
    }
}
