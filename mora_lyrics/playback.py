"""재생기에 붙이는 도우미.

가사 화면은 1초에 수십 번 「지금 어느 줄인가」를 묻는다. 그때마다 이진 탐색을 새로 하는 것은
틀리지는 않지만 필요가 없다 — 시간은 대개 앞으로만 흐르므로, 저번에 있던 자리에서 한두 칸만
보면 된다. 뒤로 감았을 때만 다시 찾는다.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import Alignment, Line, Word


@dataclass(frozen=True, slots=True)
class Moment:
    """그 순간 화면이 알아야 하는 것 전부."""

    line: Line | None
    word: Word | None
    #: 지금 줄이 어디까지 왔나(0.0~1.0). 줄이 없으면 0.0.
    progress: float
    #: 다음 줄이 시작하기까지 남은 시간(ms). 다음 줄이 없으면 None.
    until_next_ms: int | None
    #: 지금 줄의 자리. 줄이 없으면 None.
    line_number: int | None


class Playhead:
    """한 곡을 따라가며 지금 불리는 줄과 낱말을 알려 준다.

    @example
        head = Playhead(alignment)
        while playing:
            now = head.at(player.position_ms)
            if now.line is not None:
                draw(now.line.text, now.progress)
    """

    def __init__(self, alignment: Alignment) -> None:
        self.alignment = alignment
        self._line = 0
        self._word = 0

    def at(self, position_ms: int) -> Moment:
        """그 순간의 줄·낱말과 진행도.

        @param {int} position_ms - 재생기가 말하는 지금(ms).
        @returns {Moment} 그때 화면이 알아야 하는 것.
        """
        lines = self.alignment.lines
        words = self.alignment.words
        self._line = _walk(lines, self._line, position_ms)
        self._word = _walk(words, self._word, position_ms)

        line = lines[self._line] if 0 <= self._line < len(lines) else None
        if line is not None and not (line.start_ms <= position_ms < line.end_ms):
            line = None
        word = words[self._word] if 0 <= self._word < len(words) else None
        if word is not None and not (word.start_ms <= position_ms < word.end_ms):
            word = None

        progress = 0.0
        if line is not None and line.duration_ms > 0:
            progress = min(1.0, max(0.0, (position_ms - line.start_ms) / line.duration_ms))

        after = None
        for one in lines[max(0, self._line):]:
            if one.start_ms > position_ms:
                after = one.start_ms - position_ms
                break

        return Moment(
            line=line,
            word=word,
            progress=progress,
            until_next_ms=after,
            line_number=None if line is None else self._line,
        )


def _walk(items, spot: int, position_ms: int) -> int:
    """Move a cursor to the last item that starts at or before a moment.

    앞으로 흐르면 한 칸씩 나아가고, 뒤로 감았으면 뒤로 물러난다. 한 번에 멀리 건너뛰어도
    맞기는 하지만, 그럴 때만 값이 든다 — 보통은 0~1 칸이다.

    @param {Sequence} items - Words or lines in time order.
    @param {int} spot - Where the cursor was.
    @param {int} position_ms - The moment now.
    @returns {int} Where the cursor should be.
    """
    if not items:
        return 0
    spot = min(max(spot, 0), len(items) - 1)
    while spot + 1 < len(items) and items[spot + 1].start_ms <= position_ms:
        spot += 1
    while spot > 0 and items[spot].start_ms > position_ms:
        spot -= 1
    return spot
