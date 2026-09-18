"""재생 도우미 — 앞으로 흐를 때도 뒤로 감을 때도 같은 답을 내야 한다."""

from __future__ import annotations

from mora_lyrics import Playhead
from mora_lyrics.models import Alignment, Line, Word


def song() -> Alignment:
    """Build a small alignment by hand.

    @returns {Alignment} Three lines with words in them.
    """
    words = (
        Word("새빨간", 1000, 1400, False, 0, 3, 0),
        Word("노을에", 1400, 2000, False, 4, 7, 1),
        Word("금방이면", 2500, 3200, False, 9, 13, 2),
        Word("사라질", 3200, 4000, False, 14, 16, 3),
        Word("오", 6000, 7500, False, 18, 19, 4),
    )
    lines = (
        Line("새빨간 노을에", 1000, 2000, words[:2], 0, 7, 0),
        Line("금방이면 사라질", 2500, 4000, words[2:4], 9, 16, 1),
        Line("오", 6000, 7500, words[4:], 18, 19, 2),
    )
    return Alignment(tier="word", confidence=1.0, tokenizer="unilab-v2", alignment_id=1,
                     lines=lines, words=words, text="")


def test_it_follows_the_song_forward():
    head = Playhead(song())
    assert head.at(500).line is None                 # 앞 간주
    assert head.at(1200).line.text == "새빨간 노을에"
    assert head.at(1200).word.text == "새빨간"
    assert head.at(1600).word.text == "노을에"
    assert head.at(2200).line is None                # 줄 사이
    assert head.at(3000).line.text == "금방이면 사라질"
    assert head.at(9000).line is None                # 끝난 뒤


def test_seeking_backwards_gives_the_same_answer():
    # 커서를 두고 따라가므로, 뒤로 감았을 때 앞선 자리에 갇히면 안 된다.
    head = Playhead(song())
    head.at(7000)
    assert head.at(1200).line.text == "새빨간 노을에"
    assert head.at(3000).line.text == "금방이면 사라질"


def test_progress_runs_from_start_to_end_of_the_line():
    head = Playhead(song())
    assert head.at(1000).progress == 0.0
    assert abs(head.at(1500).progress - 0.5) < 1e-9
    assert head.at(1999).progress > 0.99


def test_it_says_how_long_until_the_next_line():
    head = Playhead(song())
    assert head.at(2200).until_next_ms == 300        # 다음 줄이 2500 에 시작한다
    assert head.at(1000).until_next_ms == 1500
    assert head.at(7400).until_next_ms is None       # 뒤에 줄이 없다


def test_an_empty_song_does_not_break():
    empty = Alignment(tier="none", confidence=0.0, tokenizer="", alignment_id=0, lines=(), words=(), text="")
    now = Playhead(empty).at(1234)
    assert now.line is None and now.word is None and now.progress == 0.0 and now.until_next_ms is None
