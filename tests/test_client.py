"""서버 없이 계약을 지키는지 본다 — 자리 맞추기, 화자, 오류 가르기."""

from __future__ import annotations


import pytest

from mora_lyrics import Alignment, Mora, NotAligned, to_lrc, to_srt, to_vtt
from mora_lyrics.client import _identify, _parse
from mora_lyrics.models import Token

TEXT = "새빨간 노을에\n금방이면 사라질"

#: 서버가 실제로 내는 꼴. `spans` 는 **시각이 붙은 토큰만** 담는다 — 여기서는 「노을에」가 빠졌다.
#: 그래서 배열 자리(0,1,2)와 토큰 번호(0,2,3)가 어긋난다. 이 어긋남이 화자를 엉뚱한 낱말에
#: 붙이던 버그의 원인이었다.
PAYLOAD = {
    "tier": "word",
    "confidence": 0.97,
    "tokenizer": "unilab-v2",
    "offset_unit": "codepoint",
    "alignment_id": 421,
    "lines": [[0, 7, 1000, 2000], [8, 16, 2500, 4000]],
    "spans": [[0, 3, 1000, 1400, 0], [8, 12, 2500, 3200, 0], [13, 16, 3200, 4000, 1]],
    "speaker_turns": [[0, 1000, 2000, 0.9], [1, 2500, 4000, 0.8]],
    "word_speakers": [[0, 0, 0.9], [2, 1, 0.8], [3, 1, 0.8]],
    "line_speakers": [[0, 0, 0.9], [1, 1, 0.8]],
}

#: 같은 글을 서버와 같은 방식으로 자른 것. 「노을에」가 1번이라 뒤가 하나씩 밀린다.
TOKENS = [
    Token("새빨간", 0, 3, 0),
    Token("노을에", 4, 7, 0),
    Token("금방이면", 8, 12, 1),
    Token("사라질", 13, 16, 1),
]


def test_identifier_is_exclusive_and_ordered():
    # 서버가 ISRC 를 보면 나머지를 아예 안 읽는다. 여기서도 하나만 실어야 한다.
    assert _identify("KRA401200001", "x", "가수", "곡", 1000) == {"isrc": "KRA401200001"}
    assert _identify(None, "b2d7…", "가수", "곡", 1000) == {"mbid": "b2d7…"}
    assert _identify(None, None, "가수", "곡", 1000) == {"artist": "가수", "title": "곡", "duration_ms": 1000}


def test_identifier_refuses_artist_without_length():
    # 길이가 없으면 서버가 DURATION_REQUIRED 로 거절한다. 그 전에 여기서 막는다.
    with pytest.raises(ValueError):
        _identify(None, None, "가수", "곡", None)


def test_words_carry_the_text_they_were_cut_from():
    got = _parse(PAYLOAD, TEXT, None)
    assert [one.text for one in got.words] == ["새빨간", "금방이면", "사라질"]
    assert [one.start_ms for one in got.words] == [1000, 2500, 3200]
    assert got.words[2].interpolated is True
    assert got.words[0].interpolated is False


def test_speakers_are_left_empty_without_tokens():
    # 잘못 붙이느니 비워 둔다. 예전에는 배열 자리를 번호로 써서 엉뚱한 낱말에 붙었다.
    got = _parse(PAYLOAD, TEXT, None)
    assert [one.speaker for one in got.words] == [None, None, None]
    assert [one.speaker for one in got.lines] == [None, None]


def test_speakers_land_on_the_right_word_with_tokens():
    got = _parse(PAYLOAD, TEXT, TOKENS)
    # 토큰 번호 0·2·3 이 각각 화자 0·1·1 이다. 배열 자리로 붙였다면 0·1·1 대신 0·1·None 이 됐다.
    assert [one.token for one in got.words] == [0, 2, 3]
    assert [one.speaker for one in got.words] == [0, 1, 1]
    assert [one.index for one in got.lines] == [0, 1]
    assert [one.speaker for one in got.lines] == [0, 1]


def test_lines_hold_the_words_inside_them():
    got = _parse(PAYLOAD, TEXT, TOKENS)
    assert [one.text for one in got.lines[0].words] == ["새빨간"]
    assert [one.text for one in got.lines[1].words] == ["금방이면", "사라질"]


def test_what_is_being_sung_now():
    got = _parse(PAYLOAD, TEXT, TOKENS)
    assert got.line_at(1500).text == "새빨간 노을에"
    assert got.word_at(1500) is None          # 낱말은 1400 에 끝났다
    assert got.word_at(1200).text == "새빨간"
    assert got.line_at(2200) is None          # 줄 사이는 비어 있다
    assert got.duration_ms == 4000
    assert got.has_word_timing is True


def test_speaker_turns_come_through():
    got = _parse(PAYLOAD, TEXT, TOKENS)
    assert len(got.speakers) == 2
    assert got.speakers[1].speaker_id == 1
    assert got.speakers[1].confidence == 0.8


def test_a_song_it_could_not_place_is_not_an_alignment():
    #: `tier: "none"` 은 200 으로 오지만 쓸 것이 없다. 부르는 쪽에서는 404 와 같은 뜻이다.
    mora = Mora()
    mora._post = lambda *a, **k: {**PAYLOAD, "tier": "none"}  # type: ignore[method-assign]
    with pytest.raises(NotAligned):
        mora.align(TEXT, isrc="KRA401200001")


def test_speakers_auto_asks_for_tokens_only_when_there_are_speakers():
    asked: list[str] = []
    mora = Mora()
    mora._post = lambda path, body, **k: (asked.append(path), PAYLOAD)[1]  # type: ignore[method-assign]
    mora.tokenize = lambda *a, **k: (asked.append("/v1/tokenize"), TOKENS)[1]  # type: ignore[method-assign]
    mora.align(TEXT, isrc="KRA401200001")
    assert "/v1/tokenize" in asked

    quiet = {**PAYLOAD, "word_speakers": [], "line_speakers": []}
    asked.clear()
    mora._post = lambda path, body, **k: (asked.append(path), quiet)[1]  # type: ignore[method-assign]
    mora.align(TEXT, isrc="KRA401200001")
    assert "/v1/tokenize" not in asked


def test_lrc_writes_words_when_it_can():
    got = _parse(PAYLOAD, TEXT, TOKENS)
    plain = to_lrc(got, enhanced=False)
    assert plain.splitlines()[0] == "[00:01.00]새빨간 노을에"
    rich = to_lrc(got)
    assert rich.splitlines()[0] == "[00:01.00]<00:01.00>새빨간"


def test_srt_and_vtt_stamps():
    got = _parse(PAYLOAD, TEXT, TOKENS)
    srt = to_srt(got).splitlines()
    assert srt[0] == "1"
    assert srt[1] == "00:00:01,000 --> 00:00:02,000"
    assert srt[2] == "새빨간 노을에"
    vtt = to_vtt(got).splitlines()
    assert vtt[0] == "WEBVTT"
    assert vtt[2] == "00:00:01.000 --> 00:00:02.000"


def test_an_hour_long_song_keeps_counting_minutes_in_lrc():
    # LRC 에는 시(hour) 칸이 없다. 61분은 61:00 이지 01:00 이 아니다.
    long = Alignment(tier="line", confidence=1.0, tokenizer="unilab-v2", alignment_id=1,
                     lines=_parse({**PAYLOAD, "lines": [[0, 7, 3_660_000, 3_665_000]], "spans": []},
                                  TEXT, None).lines, words=(), text=TEXT)
    assert to_lrc(long).startswith("[61:00.00]")
