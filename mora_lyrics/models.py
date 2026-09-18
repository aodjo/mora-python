"""정렬 결과를 담는 자료들.

서버는 숫자 배열만 돌려준다 — 오프셋과 밀리초. 글자는 부르는 쪽이 보낸 가사에 그대로 있으므로
여기서 잘라 붙인다. 오프셋은 **코드포인트** 단위이고 파이썬 문자열도 코드포인트로 세므로
`text[start:end]` 가 그대로 맞는다(자바스크립트는 UTF-16 이라 그쪽 라이브러리는 변환을 한다).
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field
from typing import Iterator, Literal, Sequence, TypeVar

#: 얼마나 잘게 붙었나. `word` 는 낱말마다, `line` 은 줄까지만, `none` 은 못 붙인 것.
Tier = Literal["word", "word-approx", "line", "none"]

#: 서버가 직접 적어 주는 형식. 이 글들은 **가사 글자를 담지 않는다** — 코드포인트 범위 숫자가
#: 들어간다. 사람이 읽을 자막이 필요하면 `Alignment.to_lrc()` 같은 것을 쓴다.
Format = Literal["spans", "lrc-a2", "lyricsfile", "ttml", "webvtt"]


@dataclass(frozen=True, slots=True)
class Word:
    """한 낱말과 그것이 불린 구간."""

    text: str
    start_ms: int
    end_ms: int
    #: 들어서 잰 것이 아니라 앞뒤 사이를 나눠 짐작한 자리.
    interpolated: bool
    #: 제출한 가사에서의 코드포인트 오프셋.
    start: int
    end: int
    #: 정렬기가 센 토큰 번호. 화자를 붙일 때 쓴다.
    token: int | None = None
    #: 누가 불렀나. 화자를 안 받아 왔거나 한 사람이 부른 곡이면 None.
    speaker: int | None = None

    @property
    def duration_ms(self) -> int:
        """@returns {int} 이 낱말이 불린 길이(ms)."""
        return self.end_ms - self.start_ms


@dataclass(frozen=True, slots=True)
class Line:
    """한 줄과 그것이 불린 구간."""

    text: str
    start_ms: int
    end_ms: int
    words: tuple[Word, ...]
    start: int
    end: int
    #: 가사에서 몇 번째 줄인가(빈 줄과 `[Verse]` 같은 머리말은 세지 않는다).
    index: int | None = None
    speaker: int | None = None

    @property
    def duration_ms(self) -> int:
        """@returns {int} 이 줄이 불린 길이(ms)."""
        return self.end_ms - self.start_ms


@dataclass(frozen=True, slots=True)
class Speaker:
    """한 사람이 이어서 부른 구간."""

    speaker_id: int
    start_ms: int
    end_ms: int
    confidence: float


@dataclass(frozen=True, slots=True)
class Token:
    """`/v1/tokenize` 가 돌려주는 낱말 한 조각."""

    text: str
    start: int
    end: int
    line: int


Timed = TypeVar("Timed", Word, Line)


def _at(items: Sequence[Timed], position_ms: int) -> Timed | None:
    """Find the item being sung at a moment, if any.

    항목은 시작 시각 순이고 겹치지 않는다. 그러니 시작 시각만 이진 탐색해 바로 앞의 것을 찾고,
    그것이 아직 안 끝났는지만 보면 된다. 간주에는 아무것도 없으므로 None 이 정상이다.

    @param {Sequence} items - Words or lines in time order.
    @param {int} position_ms - The moment to ask about.
    @returns {Word | Line | None} What is being sung then.
    """
    if not items:
        return None
    spot = bisect_right([one.start_ms for one in items], position_ms) - 1
    if spot < 0:
        return None
    found = items[spot]
    return found if position_ms < found.end_ms else None


@dataclass(frozen=True, slots=True)
class Alignment:
    """가사 전체에 시각이 붙은 결과."""

    tier: Tier
    #: 제출한 가사가 맞춰 둔 가사와 얼마나 같은가. 1.0 이면 글자까지 같다.
    confidence: float
    tokenizer: str
    alignment_id: int
    lines: tuple[Line, ...]
    words: tuple[Word, ...]
    speakers: tuple[Speaker, ...] = ()
    text: str = field(default="", repr=False)

    @property
    def has_word_timing(self) -> bool:
        """@returns {bool} 낱말마다 시각이 붙었는가."""
        return self.tier in ("word", "word-approx")

    @property
    def duration_ms(self) -> int:
        """@returns {int} 마지막 줄이 끝나는 시각(ms)."""
        return self.lines[-1].end_ms if self.lines else 0

    def line_at(self, position_ms: int) -> Line | None:
        """그 순간 불리고 있는 줄.

        @param {int} position_ms - 곡의 어느 순간(ms).
        @returns {Line | None} 그때 불리는 줄. 간주면 None.
        """
        return _at(self.lines, position_ms)

    def word_at(self, position_ms: int) -> Word | None:
        """그 순간 불리고 있는 낱말.

        @param {int} position_ms - 곡의 어느 순간(ms).
        @returns {Word | None} 그때 불리는 낱말. 없으면 None.
        """
        return _at(self.words, position_ms)

    def __iter__(self) -> Iterator[Line]:
        return iter(self.lines)

    def __len__(self) -> int:
        return len(self.lines)
