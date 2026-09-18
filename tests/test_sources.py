"""제공처에서 가사를 가져오는 쪽 — 망 없이 읽고 고르는 부분만 본다."""

from __future__ import annotations

from mora_lyrics.sources import (
    Lyrics, LyricLine, _affinity, _genie_msl, _is_title_header, comparable, html_to_text,
    parse_html, parse_lrc, pick_track, plain_from, same_artist, text_of,
)


def test_names_fold_the_same_however_they_are_written():
    assert comparable("아이유 (IU)") == comparable("아이유IU")
    assert comparable("Ｇｉｒｌ") == "girl"        # NFKC 로 전각이 반각이 된다
    assert comparable("!!!") == ""


def test_exact_title_always_beats_a_containing_one():
    # 「SWIM BTS」 검색 1위였던 「I Swim How Bts」가 진짜 「SWIM」을 밀어내던 자리.
    rows = [("a", "I Swim How Bts", "누구"), ("b", "SWIM", "BTS")]
    best = pick_track(rows, "SWIM", "BTS", lambda one: (one[1], one[2]))
    assert best[0] == "b"


def test_a_song_with_no_matching_title_is_not_picked():
    # genie 가 HOYO-MiX 질의에 Tyler, The Creator 의 「Window」를 돌려주던 자리.
    rows = [("x", "Window", "Tyler, The Creator")]
    assert pick_track(rows, "The Wolf Is Coming", "HOYO-MiX", lambda one: (one[1], one[2])) is None


def test_artist_only_breaks_ties_it_never_rejects():
    # MusicBrainz 는 「IU」, 한국 서비스는 「아이유」. 가수 불일치로 버리면 정상 곡을 다 잃는다.
    rows = [("a", "밤편지", "아이유")]
    assert pick_track(rows, "밤편지", "IU", lambda one: (one[1], one[2]))[0] == "a"
    covers = [("cover", "밤편지", "다른가수"), ("real", "밤편지", "아이유(IU)")]
    assert pick_track(covers, "밤편지", "IU", lambda one: (one[1], one[2]))[0] == "real"


def test_artist_names_written_two_ways_are_one_act():
    assert same_artist("아이유(IU)", "IU") is True
    assert same_artist("리도어 (Redoor)", "리도어") is True
    assert same_artist("검정치마", "혁오") is False
    assert same_artist(None, "IU") is False


def test_affinity_ranks_sameness():
    assert _affinity("EVERYTHING", "everything") == 2
    assert _affinity("EVERYTHING (Inst.)", "EVERYTHING") == 1
    assert _affinity("전혀 다른 곡", "EVERYTHING") == 0
    assert _affinity(None, "EVERYTHING") == 0


def test_line_breaks_survive_the_reader():
    # 글자와 자식 태그의 순서를 잃으면 `<br>` 이 글 뒤로 밀려 가사가 한 줄로 뭉친다.
    node = parse_html("<div>첫째 줄<br>둘째 줄<br/>셋째 줄</div>")
    assert html_to_text(_inner(node)).splitlines() == ["첫째 줄", "둘째 줄", "셋째 줄"]


def test_raw_text_keeps_newlines_but_text_of_does_not():
    # bugs 는 `<xmp>` 에 줄바꿈 그대로 담는다. 제목·가수는 뭉개도 되지만 가사는 안 된다.
    node = parse_html("<xmp>첫째 줄\n둘째 줄\n셋째 줄</xmp>").first("xmp")
    assert node.raw_text().strip().splitlines() == ["첫째 줄", "둘째 줄", "셋째 줄"]
    assert text_of(node) == "첫째 줄 둘째 줄 셋째 줄"


def test_a_label_span_does_not_become_part_of_the_title():
    # melon·genie 의 제목 칸에는 「곡명」 같은 딱지가 같이 들어 있다.
    node = parse_html('<div class="song_name"><strong>곡명</strong>영원은 그렇듯</div>').first("div")
    assert node.own_text() == "영원은 그렇듯"
    assert "곡명" in text_of(node)


def test_attributes_come_back_out_so_they_can_be_searched():
    # melon 의 곡 번호는 `onClick="…goSongDetail('123')"` 안에 있다.
    node = parse_html("<a onclick=\"melon.link.goSongDetail('33186501');\">곡정보</a>")
    assert "goSongDetail('33186501')" in _inner(node)


def test_entities_and_tags_are_cleared():
    # 줄마다 양끝을 다듬으므로 `&nbsp;` 가 줄 앞에 있으면 그 공백은 사라진다.
    assert html_to_text("<p>a &amp; b</p><p>&nbsp;c&#39;d</p>") == "a & b\nc'd"
    assert html_to_text("<p>a&nbsp;&nbsp;b</p>") == "a  b"


def test_lrc_is_read_into_lines():
    got = parse_lrc("[00:12.34]첫째\n[01:02.5]둘째\n태그 없는 줄")
    assert [(one.time_ms, one.text) for one in got] == [(12340, "첫째"), (62500, "둘째")]


def test_genie_keys_are_milliseconds():
    got = _genie_msl('lyrics({"12040":"둘째","400":"첫째"});')
    assert [(one.time_ms, one.text) for one in got] == [(400, "첫째"), (12040, "둘째")]
    assert _genie_msl("망가진 응답") == []


def test_genie_title_banner_is_dropped_but_a_real_first_line_is_not():
    # 「Half The World Away - Oasis」 같은 머리줄은 0ms 에 붙어 정렬을 한 줄씩 민다.
    assert _is_title_header("Half The World Away - Oasis", "Half The World Away", "Oasis") is True
    assert _is_title_header("영원은 그렇듯", "영원은 그렇듯", "리도어") is True
    # 제목으로 **시작하는** 진짜 첫 소절은 남겨야 한다.
    assert _is_title_header("Swim, swim in the dark", "Swim", "BTS") is False
    assert _is_title_header("", "Swim", "BTS") is False


def test_plain_falls_back_to_the_synced_lines():
    synced = [LyricLine(0, "첫째"), LyricLine(1000, "둘째")]
    assert plain_from(None, synced) == "첫째\n둘째"
    assert plain_from("  ", synced) == "첫째\n둘째"
    assert plain_from("있는 글", synced) == "있는 글"
    assert plain_from(None, []) == ""


def test_lyrics_prints_as_its_text():
    assert str(Lyrics(provider="vibe", lyrics="첫째\n둘째")) == "첫째\n둘째"


def _inner(node) -> str:
    """Rebuild a node's inner HTML for the tests.

    @param {Node} node - The node.
    @returns {str} Its inner HTML.
    """
    from mora_lyrics.sources import _outer
    return _outer(node)


def test_a_providers_play_time_becomes_milliseconds():
    # vibe·flo 가 「03:57」 꼴로 곡 길이를 준다. 이것이 있어야 엉뚱한 영상을 길이로 거를 수 있다.
    from mora_lyrics.sources import play_time
    assert play_time("03:57") == 237000
    assert play_time("1:02:03") == 3723000       # 한 시간이 넘는 녹음
    assert play_time(None) is None
    assert play_time("") is None
    assert play_time("모름") is None


def test_suggest_falls_back_to_the_artist_when_the_title_is_misspelt():
    # 「offically missing you」로는 저쪽 검색도 0건이다. 그때 가수로 물어야 그 곡이 보인다.
    asked: list[str] = []

    def fake(url, *, timeout, headers=None):
        asked.append(url)
        if "offically" in url:
            return {"response": {"result": {"tracks": []}}}
        return {"response": {"result": {"tracks": [
            {"trackTitle": "Officially Missing You", "artists": [{"artistName": "긱스(Geeks)"}]}]}}}

    import mora_lyrics.sources as sources
    was, sources._get_json = sources._get_json, fake
    try:
        got = sources.suggest("offically missing you", "긱스")
    finally:
        sources._get_json = was
    assert got == [("Officially Missing You", "긱스(Geeks)")]
    assert len(asked) == 2, "제목으로 먼저 묻고, 없으면 가수로 다시 묻는다"


def test_suggest_says_nothing_rather_than_guessing_when_search_is_down():
    import mora_lyrics.sources as sources

    def broken(url, *, timeout, headers=None):
        raise OSError("망이 안 된다")

    was, sources._get_json = sources._get_json, broken
    try:
        assert sources.suggest("아무 곡", "아무개") == []
    finally:
        sources._get_json = was
