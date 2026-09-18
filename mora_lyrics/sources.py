"""가사를 제공처에서 가져온다 — bugs · flo · genie · melon · vibe.

Mora 는 **타이밍만** 준다. 가사 글은 부르는 쪽이 들고 있어야 하는데, 그것을 어디서 구하느냐가
매번 막히는 자리였다. 그래서 Mora 수집기가 쓰는 길을 그대로 옮겨 왔다.

열쇠는 필요 없다. flo 와 vibe 는 JSON API 를 부르고, melon · bugs · genie 는 페이지를 읽는다.
페이지를 읽는 쪽은 저쪽이 화면을 바꾸면 깨진다 — 그때는 다른 제공처가 받아 준다.

의존성은 없다. HTML 은 표준 `html.parser` 로 읽는다.

@example
    from mora_lyrics import fetch_lyrics

    for got in fetch_lyrics("영원은 그렇듯", artist="리도어"):
        print(got.provider, len(got.lyrics.splitlines()), "줄")
"""

from __future__ import annotations

import json
import re
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Callable, Iterable, Sequence

DEFAULT_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
#: Genie 는 19금 곡의 가사를 일반 브라우저에게 숨긴다. 검색 색인을 위해 크롤러에게는 열어 두므로,
#: **가사를 읽을 때만** 크롤러로 묻는다. 실측: 12만 바이트(가사 없음) → 17만 바이트(가사 전문).
#: 검색은 이 UA 로 물으면 결과가 비므로 그대로 둔다.
CRAWLER_UA = ("Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; Googlebot/2.1; "
              "+http://www.google.com/bot.html) Chrome/150.0.0.0 Safari/537.36")
DEFAULT_TIMEOUT = 12.0
#: Melon 은 검색 화면에서 제목을 못 읽으므로 후보의 상세 페이지를 열어 확인한다.
#: 열 때마다 한 번씩 더 묻게 되므로 셋까지만 본다.
MELON_TRIES = 3


# ── 자료 ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class LyricLine:
    """시각이 붙은 가사 한 줄 — 제공처가 줄 때만 온다."""

    time_ms: int
    text: str


@dataclass(frozen=True, slots=True)
class Lyrics:
    """한 제공처가 돌려준 가사."""

    provider: str
    lyrics: str
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    url: str | None = None
    track_id: str | None = None
    #: 제공처가 말하는 곡 길이(ms). 이것이 있으면 엉뚱한 영상을 길이로 걸러낼 수 있다 —
    #: 산토리 자리에 아크라포빅 영상이 붙었던 일이 바로 그 검사가 없어서였다. JSON 제공처
    #: (vibe·flo)만 준다.
    duration_ms: int | None = None
    #: 제공처가 시각까지 주면 채워진다. Mora 에 보낼 때는 안 쓰지만, 견주어 볼 수는 있다.
    synced: tuple[LyricLine, ...] = ()

    def __str__(self) -> str:
        return self.lyrics


# ── 고르기 ────────────────────────────────────────────────────────────────


def comparable(value: str) -> str:
    """Fold a name for comparison: NFKC, lower case, letters and digits only.

    @param {str} value - The name.
    @returns {str} The folded form.
    """
    return re.sub(r"[^\w]+", "", unicodedata.normalize("NFKC", value).lower(), flags=re.UNICODE).replace("_", "")


def _affinity(candidate: str | None, wanted: str) -> int:
    """How close two titles are: 2 same, 1 one contains the other, 0 different.

    포함만으로 같은 곡 취급하면 「SWIM BTS」 검색 1위였던 「I Swim How Bts」가 진짜 「SWIM」을
    밀어낸다 — 정확 일치가 항상 이겨야 한다.

    @param {str | None} candidate - The title found.
    @param {str} wanted - The title asked for.
    @returns {int} 0, 1 or 2.
    """
    if candidate is None:
        return 0
    a, b = comparable(candidate), comparable(wanted)
    if not a or not b:
        return 0
    if a == b:
        return 2
    return 1 if a in b or b in a else 0


def same_artist(candidate: str | None, wanted: str | None) -> bool:
    """Whether two artist names overlap — 「아이유(IU)」 and 「IU」 count as one.

    @param {str | None} candidate - The name found.
    @param {str | None} wanted - The name asked for.
    @returns {bool} True when they name the same act.
    """
    if candidate is None or wanted is None:
        return False
    a, b = comparable(candidate), comparable(wanted)
    if not a or not b:
        return False
    return a == b or a in b or b in a


def pick_track(items: Sequence, wanted_title: str, wanted_artist: str | None,
               read: Callable[[object], tuple[str | None, str | None]]):
    """Pick the search result most likely to be the song that was asked for.

    제목 일치를 **필수**로 한다. 모든 제공처가 검색 첫 항목을 검증 없이 집던 시절, genie 는
    HOYO-MiX 게임 OST 질의에 Tyler, The Creator 의 「Window」를 돌려줬고 melon 은 라틴어 가사
    하나를 88곡에 붙였다. 관측된 오염은 전부 제목 불일치였다.

    가수는 **선호 신호로만** 쓴다. MusicBrainz 는 「IU」를 주고 한국 서비스는 「아이유」를
    보여주므로, 가수 불일치만으로 버리면 표기가 다른 정상 곡을 전부 잃는다.

    @param {Sequence} items - Search results.
    @param {str} wanted_title - The title asked for.
    @param {str | None} wanted_artist - The artist asked for, if known.
    @param {Callable} read - Reads (title, artist) out of one result.
    @returns {object | None} The best result, or None when no title matches.
    """
    best, best_rank = None, 0
    for item in items:
        title, artist = read(item)
        affinity = _affinity(title, wanted_title)
        if affinity == 0:
            continue
        rank = affinity * 2 + (1 if same_artist(artist, wanted_artist) else 0)
        if rank > best_rank:
            best, best_rank = item, rank
    return best


# ── 글 다듬기 ─────────────────────────────────────────────────────────────

_ENTITIES = {"&nbsp;": " ", "&amp;": "&", "&lt;": "<", "&gt;": ">", "&#39;": "'", "&apos;": "'", "&quot;": '"'}


def html_to_text(html: str) -> str:
    """Turn a lyric fragment of HTML into plain lines.

    @param {str} html - The fragment.
    @returns {str} Plain text, one line per sung line.
    """
    out = re.sub(r"<!--.*?-->", "", html, flags=re.S)
    out = re.sub(r"<br\s*/?>", "\n", out, flags=re.I)
    out = re.sub(r"</(p|div)>", "\n", out, flags=re.I)
    out = re.sub(r"<[^>]+>", "", out)
    for mark, plain in _ENTITIES.items():
        out = out.replace(mark, plain)
    out = "\n".join(one.strip() for one in out.split("\n"))
    return re.sub(r"\n{3,}", "\n\n", out).strip()


def parse_lrc(lrc: str) -> list[LyricLine]:
    """Read `[mm:ss.xx] 가사` lines.

    @param {str} lrc - The LRC text.
    @returns {list[LyricLine]} Lines in time order.
    """
    out: list[LyricLine] = []
    for row in lrc.splitlines():
        tags = re.findall(r"\[(\d{1,2}):(\d{2})(?:[.:](\d{1,3}))?\]", row)
        if not tags:
            continue
        text = re.sub(r"\[[^\]]*\]", "", row).strip()
        for minute, second, fraction in tags:
            frac = int((fraction + "00")[:3]) if fraction else 0
            out.append(LyricLine((int(minute) * 60 + int(second)) * 1000 + frac, text))
    return sorted(out, key=lambda one: one.time_ms)


def play_time(value: str | None) -> int | None:
    """Read a provider's `03:57` into milliseconds.

    vibe 와 flo 가 곡 길이를 이 꼴로 준다. 시(hour)까지 오는 곡도 있으므로 칸 수로 센다.

    @param {str | None} value - The `mm:ss` or `hh:mm:ss` reading.
    @returns {int | None} Milliseconds, or None when it is not a time.
    """
    if not value:
        return None
    parts = str(value).strip().split(":")
    if not all(one.isdigit() for one in parts) or not 2 <= len(parts) <= 3:
        return None
    whole = 0
    for one in parts:
        whole = whole * 60 + int(one)
    return whole * 1000


def plain_from(plain: str | None, synced: Sequence[LyricLine] | None) -> str:
    """Take the plain lyric if there is one, else build it from the synced lines.

    @param {str | None} plain - The plain text a provider gave.
    @param {Sequence[LyricLine] | None} synced - Its synced lines.
    @returns {str} The lyric text.
    """
    if (plain or "").strip():
        return (plain or "").strip()
    if synced:
        return "\n".join(one.text for one in synced).strip()
    return ""


# ── 작은 DOM ──────────────────────────────────────────────────────────────


@dataclass
class Node:
    """A tag, what it carried, and what was inside it — **in the order it appeared**.

    글자와 자식 태그를 한 목록에 순서대로 담는다. 처음에는 글자를 한 칸에 모아 두었는데, 그러면
    `가사<br>가사` 의 `<br>` 이 글 뒤로 밀려 줄바꿈이 통째로 사라졌다 — melon 가사가 한 줄로
    뭉쳐 나온 것이 그 탓이다.
    """

    tag: str
    attrs: dict[str, str]
    parts: list = field(default_factory=list)

    @property
    def children(self) -> list["Node"]:
        """@returns {list[Node]} 이 마디 바로 아래의 태그들."""
        return [one for one in self.parts if isinstance(one, Node)]

    @property
    def text(self) -> str:
        """@returns {str} 이 마디에 바로 들어 있는 글자. 자식 태그의 글자는 안 센다."""
        return "".join(one for one in self.parts if isinstance(one, str))

    def has_class(self, name: str) -> bool:
        """@param {str} name - The class to look for. @returns {bool} Whether it carries it."""
        return name in (self.attrs.get("class") or "").split()

    def find(self, tag: str | None = None, *, cls: str | None = None, attr: str | None = None,
             holds: str | None = None) -> Iterable["Node"]:
        """Walk this node and everything under it, handing back what matches.

        @param {str | None} tag - Tag name to match.
        @param {str | None} cls - Class the tag must carry.
        @param {str | None} attr - Attribute the tag must have.
        @param {str | None} holds - Text an `href` must contain.
        @returns {Iterable[Node]} Matching nodes, outermost first.
        """
        for one in self.children:
            fits = (tag is None or one.tag == tag) \
                and (cls is None or one.has_class(cls)) \
                and (attr is None or attr in one.attrs) \
                and (holds is None or holds in (one.attrs.get("href") or ""))
            if fits:
                yield one
            yield from one.find(tag, cls=cls, attr=attr, holds=holds)

    def first(self, *args, **kwargs) -> "Node | None":
        """@returns {Node | None} The first match of `find`, or None."""
        return next(iter(self.find(*args, **kwargs)), None)

    def own_text(self) -> str:
        """Text directly inside this tag, not counting nested tags.

        Genie·Melon 의 제목 칸에는 「곡명」 같은 딱지 span 이 같이 들어 있다. 그것까지 읽으면
        제목이 달라져 곡을 못 고른다.

        @returns {str} The text of this node alone.
        """
        return self.text.strip()

    def raw_text(self) -> str:
        """All text under this node **with its line breaks kept**.

        Bugs 는 가사를 `<xmp>` 에 줄바꿈 그대로 담는다. 공백을 뭉개면 스물여덟 줄이 한 줄이 된다.

        @returns {str} The text, newlines intact.
        """
        out = []
        for one in self.parts:
            out.append(one if isinstance(one, str) else one.raw_text())
        return "".join(out)


class _Tree(HTMLParser):
    """Build a `Node` tree. 가사 페이지를 읽는 데 필요한 만큼만."""

    #: 닫는 짝이 없는 태그. 이것을 안 챙기면 트리가 통째로 한쪽으로 기운다.
    VOID = {"br", "img", "input", "meta", "link", "hr", "source", "area", "base", "col", "embed", "track", "wbr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("#root", {})
        self._stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = Node(tag, {name: (value or "") for name, value in attrs})
        self._stack[-1].parts.append(node)
        #: 닫는 짝이 없는 태그는 쌓지 않는다. 다만 **버리지도 않는다** — 버리면 `<br>` 이 사라져
        #: 가사가 한 줄로 뭉친다.
        if tag not in self.VOID:
            self._stack.append(node)

    def handle_endtag(self, tag: str) -> None:
        for spot in range(len(self._stack) - 1, 0, -1):
            if self._stack[spot].tag == tag:
                del self._stack[spot:]
                return

    def handle_data(self, data: str) -> None:
        self._stack[-1].parts.append(data)


def parse_html(html: str) -> Node:
    """Read a page into nodes.

    @param {str} html - The page.
    @returns {Node} Its root.
    """
    tree = _Tree()
    tree.feed(html)
    return tree.root


def text_of(node: Node | None) -> str:
    """All text under a node, tags flattened.

    @param {Node | None} node - The node.
    @returns {str} Its text.
    """
    if node is None:
        return ""
    return re.sub(r"\s+", " ", node.raw_text()).strip()


# ── 가져오기 ──────────────────────────────────────────────────────────────


def _get(url: str, *, timeout: float, headers: dict[str, str] | None = None) -> str:
    """Fetch a page or an API answer.

    @param {str} url - Where to ask.
    @param {float} timeout - Seconds to wait.
    @param {dict | None} headers - Extra headers.
    @returns {str} The body.
    @throws {urllib.error.URLError} When it does not answer.
    """
    request = urllib.request.Request(url, headers={
        "User-Agent": DEFAULT_UA,
        "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        **(headers or {}),
    })
    with urllib.request.urlopen(request, timeout=timeout) as answer:
        raw = answer.read()
    charset = "utf-8"
    return raw.decode(charset, "replace")


def _get_json(url: str, *, timeout: float, headers: dict[str, str] | None = None):
    """Fetch and parse JSON, not trusting the content type.

    일부 API 가 헤더를 틀리게 보낸다.

    @param {str} url - Where to ask.
    @param {float} timeout - Seconds to wait.
    @param {dict | None} headers - Extra headers.
    @returns {object} The parsed body.
    """
    return json.loads(_get(url, timeout=timeout, headers=headers))


def _query(title: str, artist: str | None) -> str:
    """@returns {str} What to type into a provider's search box."""
    return urllib.parse.quote(" ".join(one for one in (title, artist) if one))


# ── 제공처 ────────────────────────────────────────────────────────────────


def melon(title: str, artist: str | None = None, *, timeout: float = DEFAULT_TIMEOUT) -> Lyrics | None:
    """Melon — 통합검색에서 곡을 고르고 상세 페이지에서 가사를 읽는다.

    통합검색(total)만 결과를 서버에서 그려 준다. `song/index.htm` 은 JS 로 채워지므로 못 읽는다.

    @param {str} title - Song name.
    @param {str | None} artist - Performer, to tell covers apart.
    @param {float} timeout - Seconds to wait per request.
    @returns {Lyrics | None} The lyric, or None when this provider does not have it.
    """
    base = "https://www.melon.com"
    head = {"Referer": f"{base}/"}
    html = _get(f"{base}/search/total/index.htm?q={_query(title, artist)}&section=song",
                timeout=timeout, headers=head)
    #: 검색 결과에서 곡 번호만 뽑는다. 예전에는 `<tr>` 행마다 제목·가수가 함께 있었는데 화면이
    #: `<li>` 로 바뀌면서 그 길이 끊겼다(2026-09 확인). 번호만 뽑고 제목은 **상세 페이지 제 것**을
    #: 읽는 편이 화면이 또 바뀌어도 버틴다 — 대신 후보마다 한 번씩 더 묻게 되므로 셋까지만 본다.
    seen: list[str] = []
    for found in re.finditer(r"goSongDetail\('(\d+)'\)", html):
        if found.group(1) not in seen:
            seen.append(found.group(1))
    for song_id in seen[:MELON_TRIES]:
        detail = f"{base}/song/detail.htm?songId={song_id}"
        page = parse_html(_get(detail, timeout=timeout, headers=head))
        #: 제목 칸에는 「곡명」 같은 딱지 span 이 같이 있다. 그 마디의 **제 글자**만 읽는다.
        holder = page.first("div", cls="song_name") or page.first("div", cls="songname")
        found_title = (holder.own_text() if holder else "") or text_of(holder)
        found_title = re.sub(r"^곡명\s*", "", found_title).strip()
        singer = text_of(page.first("div", cls="artist"))
        singer = re.sub(r"\s*-?\s*페이지 이동$", "", singer).strip()
        if _affinity(found_title, title) == 0:
            continue
        holder = next((one for one in page.find(attr="id") if one.attrs.get("id") == "d_video_summary"), None) \
            or page.first("div", cls="lyric")
        lyrics = html_to_text(_outer(holder)) if holder else ""
        if not lyrics:
            continue
        return Lyrics(provider="melon", lyrics=lyrics, title=found_title or title, artist=singer or artist,
                      url=detail, track_id=song_id)
    return None


def bugs(title: str, artist: str | None = None, *, timeout: float = DEFAULT_TIMEOUT) -> Lyrics | None:
    """Bugs — 트랙 검색 목록에서 고르고 트랙 페이지의 가사 칸을 읽는다.

    @param {str} title - Song name.
    @param {str | None} artist - Performer.
    @param {float} timeout - Seconds to wait per request.
    @returns {Lyrics | None} The lyric, or None.
    """
    base = "https://music.bugs.co.kr"
    head = {"Referer": f"{base}/"}
    page = parse_html(_get(f"{base}/search/track?q={_query(title, artist)}", timeout=timeout, headers=head))
    rows = []
    for row in page.find("tr", attr="trackid"):
        #: 제목은 `p.title` **안의** a 다. 행의 첫 a 를 집으면 앨범 표지 링크가 걸린다.
        holder = row.first("p", cls="title")
        link = holder.first("a") if holder else None
        name = (link.attrs.get("title") if link else None) or text_of(link)
        if not name.strip():
            continue
        singer = row.first("p", cls="artist")
        rows.append((row.attrs["trackid"], name.strip(), text_of(singer.first("a") if singer else singer)))
    best = pick_track(rows, title, artist, lambda one: (one[1], one[2]))
    if best is None:
        return None

    track_id = best[0]
    where = f"{base}/track/{track_id}"
    page = parse_html(_get(where, timeout=timeout, headers=head))
    holder = page.first("div", cls="lyricsContainer")
    #: `<xmp>` 안에는 줄바꿈이 그대로 있다. 공백을 뭉개면 스물여덟 줄이 한 줄이 된다.
    plain = holder.first("xmp") if holder else None
    lyrics = plain.raw_text().strip() if plain else html_to_text(_outer(holder))
    if not lyrics.strip():
        return None
    return Lyrics(provider="bugs", lyrics=lyrics.strip(), title=best[1] or title, artist=best[2] or artist,
                  url=where, track_id=track_id)


def genie(title: str, artist: str | None = None, *, timeout: float = DEFAULT_TIMEOUT) -> Lyrics | None:
    """Genie — 검색에서 고르고, 시각이 붙은 가사를 `get_msl.asp` 에서 받는다.

    19금 곡의 가사는 일반 브라우저에게 안 보여 준다. **가사를 읽을 때만** 크롤러 UA 로 묻는다 —
    검색을 그 UA 로 물으면 결과가 비기 때문에 검색은 그대로 둔다.

    @param {str} title - Song name.
    @param {str | None} artist - Performer.
    @param {float} timeout - Seconds to wait per request.
    @returns {Lyrics | None} The lyric, or None.
    """
    base = "https://www.genie.co.kr"
    head = {"Referer": f"{base}/"}
    page = parse_html(_get(f"{base}/search/searchMain?query={_query(title, artist)}", timeout=timeout, headers=head))
    rows = []
    for row in page.find("tr", attr="songid"):
        link = row.first("a", cls="title")
        name = (link.own_text() if link else "") or (link.attrs.get("title", "").strip() if link else "")
        if not name:
            continue
        rows.append((row.attrs["songid"], name,
                     text_of(row.first("a", cls="artist")), text_of(row.first("a", cls="albumtitle")) or None))
    best = pick_track(rows, title, artist, lambda one: (one[1], one[2]))
    if best is None:
        return None

    song_id = best[0]
    synced: list[LyricLine] = []
    lyrics = ""
    try:
        raw = _get(f"https://dn.genie.co.kr/app/purchase/get_msl.asp?path=a&songid={song_id}",
                   timeout=timeout, headers=head)
        synced = _genie_msl(raw)
        if synced and _is_title_header(synced[0].text, best[1], best[2]):
            synced = synced[1:]
        lyrics = "\n".join(one.text for one in synced)
    except Exception:
        pass  # 시각 가사가 없는 곡 — 아래에서 페이지를 읽는다

    detail = f"{base}/detail/songInfo?xgnm={song_id}"
    if not lyrics.strip():
        page = parse_html(_get(detail, timeout=timeout, headers={**head, "User-Agent": CRAWLER_UA}))
        holder = next((one for one in page.find("p") if _in_id(page, one, "pLyrics")), None) \
            or page.first("div", cls="lyrics")
        rows_out = html_to_text(_outer(holder)).split("\n") if holder else []
        if rows_out and _is_title_header(rows_out[0], best[1], best[2]):
            rows_out = rows_out[1:]
        lyrics = "\n".join(rows_out).strip()
    if not lyrics.strip():
        return None
    return Lyrics(provider="genie", lyrics=plain_from(lyrics, synced), title=best[1] or title,
                  artist=best[2] or artist, album=best[3], url=detail, track_id=song_id,
                  synced=tuple(synced))


def flo(title: str, artist: str | None = None, *, timeout: float = DEFAULT_TIMEOUT) -> Lyrics | None:
    """FLO — JSON 검색으로 고르고 트랙 상세의 `lyrics` 를 받는다.

    검색은 `keyword` 만 붙여야 결과가 나온다 — 부가 파라미터를 붙이면 빈 결과가 온다.

    @param {str} title - Song name.
    @param {str | None} artist - Performer.
    @param {float} timeout - Seconds to wait per request.
    @returns {Lyrics | None} The lyric, or None.
    """
    base = "https://www.music-flo.com"
    head = {"Referer": f"{base}/", "Accept": "application/json"}
    found = _get_json(f"{base}/api/search/v2/search?keyword={_query(title, artist)}", timeout=timeout, headers=head)
    groups = ((found or {}).get("data") or {}).get("list") or []
    group = next((one for one in groups if one.get("type") == "TRACK"), groups[0] if groups else {})
    best = pick_track(group.get("list") or [], title, artist,
                      lambda one: (one.get("name"), ", ".join(a.get("name") or "" for a in one.get("artistList") or [])))
    if best is None:
        return None

    track_id = str(best.get("id"))
    meta = ((_get_json(f"{base}/api/meta/v1/track/{track_id}", timeout=timeout, headers=head) or {}).get("data")) or {}
    synced: list[LyricLine] = []
    if isinstance(meta.get("lyricsList"), list) and meta["lyricsList"]:
        synced = [LyricLine(int(one.get("timeMillis") or one.get("time") or 0), one.get("text") or "")
                  for one in meta["lyricsList"]]
    elif meta.get("lyrics") and re.search(r"\[\d{1,2}:\d{2}", meta["lyrics"]):
        synced = parse_lrc(meta["lyrics"])

    plain = re.sub(r"\[[^\]]*\]", "", meta.get("lyrics") or "").strip()
    lyrics = plain_from(plain, synced)
    if not lyrics:
        return None
    credited = [one.get("name") or "" for one in (best.get("artistList") or []) if one.get("name")]
    return Lyrics(provider="flo", lyrics=lyrics, title=meta.get("name") or best.get("name") or title,
                  artist=", ".join(credited) if credited else artist,
                  duration_ms=play_time(best.get("playTime") or meta.get("playTime")),
                  url=f"{base}/detail/track/{track_id}/detailinfo", track_id=track_id, synced=tuple(synced))


def vibe(title: str, artist: str | None = None, *, timeout: float = DEFAULT_TIMEOUT) -> Lyrics | None:
    """Vibe (네이버) — JSON 검색으로 고르고 `lyric` 에서 평문과 시각 가사를 받는다.

    시각 가사는 **나란한 두 배열**이다: `startTimeIndex[i]`(초, 실수)가 `contents[0].text[i]` 와
    짝이다. 예전에는 `lyricLine: [{startTimeMillis, text}]` 였는데 그 모양은 이제 오지 않는다 —
    `hasSyncLyric` 은 여전히 true 로 오므로 깃발만 보아서는 알 수 없다.

    @param {str} title - Song name.
    @param {str | None} artist - Performer.
    @param {float} timeout - Seconds to wait per request.
    @returns {Lyrics | None} The lyric, or None.
    """
    api = "https://apis.naver.com/vibeWeb/musicapiweb"
    head = {"Referer": "https://vibe.naver.com/", "Accept": "application/json"}
    found = _get_json(f"{api}/v3/search/track?query={_query(title, artist)}&start=1&display=10&sort=RELEVANCE",
                      timeout=timeout, headers=head)
    tracks = (((found or {}).get("response") or {}).get("result") or {}).get("tracks") or []
    best = pick_track(tracks, title, artist,
                      lambda one: (one.get("trackTitle"),
                                   ", ".join(a.get("artistName") or "" for a in one.get("artists") or [])))
    if best is None:
        return None

    track_id = str(best.get("trackId"))
    answer = _get_json(f"{api}/v3/lyric/{track_id}", timeout=timeout, headers=head)
    lyric = ((((answer or {}).get("response") or {}).get("result") or {}).get("lyric")) or {}

    synced: list[LyricLine] = []
    sync = lyric.get("syncLyric") or {}
    times = sync.get("startTimeIndex")
    contents = sync.get("contents") or []
    # 언어가 여럿일 수 있다. 원문(default)을 쓰고, 없으면 첫 번째를 쓴다.
    body = (next((one for one in contents if one.get("languageType") == "default"),
                 contents[0] if contents else {}) or {}).get("text")
    if isinstance(times, list) and isinstance(body, list) and times:
        # 길이가 어긋나면 짧은 쪽까지만. 짝이 없는 시각에는 붙일 글자가 없다.
        synced = [LyricLine(round(float(times[k] or 0) * 1000), body[k] or "")
                  for k in range(min(len(times), len(body))) if (body[k] or "").strip()]

    lyrics = plain_from((lyric.get("normalLyric") or {}).get("text"), synced)
    if not lyrics:
        return None
    credited = [one.get("artistName") or "" for one in (best.get("artists") or []) if one.get("artistName")]
    return Lyrics(provider="vibe", lyrics=lyrics, title=best.get("trackTitle") or title,
                  artist=", ".join(credited) if credited else artist,
                  album=(best.get("album") or {}).get("albumTitle"),
                  duration_ms=play_time(best.get("playTime")),
                  url=f"https://vibe.naver.com/track/{track_id}", track_id=track_id, synced=tuple(synced))


#: 부르는 순서. 앞의 둘은 JSON API 라 잘 안 깨지므로 먼저 묻는다.
PROVIDERS: dict[str, Callable[..., Lyrics | None]] = {
    "vibe": vibe, "flo": flo, "melon": melon, "genie": genie, "bugs": bugs,
}


def fetch_lyrics(title: str, artist: str | None = None, *, providers: Sequence[str] | None = None,
                 timeout: float = DEFAULT_TIMEOUT, first: bool = False) -> list[Lyrics]:
    """제공처들에게 가사를 물어 받아 온 것을 모두 돌려준다.

    한 곳이 막혀도 나머지로 간다 — 페이지를 읽는 쪽은 저쪽이 화면을 바꾸면 깨지기 때문이다.
    여럿을 받아 두면 Mora 에 어느 글을 보낼지 고를 수 있고, 제공처마다 표기가 조금씩 다르므로
    가장 잘 맞는 것이 confidence 로 드러난다.

    @param {str} title - 곡 이름.
    @param {str | None} artist - 가수 이름. 같은 제목의 다른 곡을 가려낸다.
    @param {Sequence[str] | None} providers - 물어볼 곳. 비우면 다섯 곳 모두.
    @param {float} timeout - 한 요청이 기다릴 초.
    @param {bool} first - True 면 하나 받는 즉시 멈춘다.
    @returns {list[Lyrics]} 받아 온 가사들. 하나도 못 받으면 빈 목록.
    """
    out: list[Lyrics] = []
    for name in (providers or PROVIDERS.keys()):
        provider = PROVIDERS.get(name)
        if provider is None:
            continue
        try:
            got = provider(title, artist, timeout=timeout)
        except Exception:
            continue  # 한 곳이 막힌 것으로 전체를 멈추지 않는다
        if got is not None and got.lyrics.strip():
            out.append(got)
            if first:
                break
    return out


# ── 안쪽 ──────────────────────────────────────────────────────────────────


def _outer(node: Node | None) -> str:
    """Rebuild a node's inner HTML, enough for `html_to_text` to read.

    @param {Node | None} node - The node.
    @returns {str} Its inner HTML.
    """
    if node is None:
        return ""
    out = []
    for one in node.parts:
        if isinstance(one, str):
            out.append(one)
            continue
        #: 속성도 실어야 한다 — `onClick="…goSongDetail('123')"` 처럼 찾을 것이 속성 안에 있다.
        attrs = "".join(f' {name}="{value}"' for name, value in one.attrs.items())
        out.append(f"<{one.tag}{attrs}>{_outer(one)}</{one.tag}>")
    return "".join(out)


def _in_class(row: Node, want: Node, cls: str) -> bool:
    """Whether a node sits under an element carrying a class.

    @param {Node} row - Where to look from.
    @param {Node} want - The node to place.
    @param {str} cls - The class of the wrapper.
    @returns {bool} True when it does.
    """
    for holder in row.find(cls=cls):
        if want is holder or any(one is want for one in holder.find()):
            return True
    return False


def _in_id(page: Node, want: Node, ident: str) -> bool:
    """Whether a node sits under an element with an id.

    @param {Node} page - Where to look from.
    @param {Node} want - The node to place.
    @param {str} ident - The id of the wrapper.
    @returns {bool} True when it does.
    """
    for holder in page.find(attr="id"):
        if holder.attrs.get("id") == ident and (want is holder or any(one is want for one in holder.find())):
            return True
    return False


def _genie_msl(raw: str) -> list[LyricLine]:
    """Read Genie's `{ "400": "line", "12040": "line" }` — the key is the millisecond.

    @param {str} raw - The JSONP body.
    @returns {list[LyricLine]} Lines in time order.
    """
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end < 0:
        return []
    try:
        got = json.loads(raw[start:end + 1])
    except Exception:
        return []
    out = []
    for key, text in got.items():
        try:
            out.append(LyricLine(int(key), str(text)))
        except (TypeError, ValueError):
            continue
    return sorted(out, key=lambda one: one.time_ms)


def _is_title_header(line: str | None, title: str, artist: str | None) -> bool:
    """Whether the first line is really the song's title banner.

    Genie 는 곡에 따라 가사 맨 앞에 「Half The World Away - Oasis」 같은 줄을 넣는다. 시각 가사에서는
    0ms 에 붙어 있어 그대로 두면 정렬이 통째로 한 줄씩 밀린다. 제목(+가수)과 **정확히** 같을 때만
    버린다 — 「Swim, swim」처럼 제목으로 시작하는 진짜 첫 소절은 남겨야 한다.

    @param {str | None} line - The first line.
    @param {str} title - The song's title.
    @param {str | None} artist - Its artist.
    @returns {bool} True when the line is a banner, not singing.
    """
    if not line:
        return False
    first = comparable(line)
    if not first:
        return False
    return first == comparable(f"{title}{artist or ''}") or first == comparable(title)
