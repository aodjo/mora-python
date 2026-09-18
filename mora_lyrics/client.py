"""Mora 공개 정렬 API 클라이언트.

의존성이 없다 — 표준 라이브러리의 `urllib` 만 쓴다. 가사 재생기를 만드는 사람이 이것 하나
때문에 `requests` 를 끌어오게 하지 않으려는 것이다.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Literal

from .models import Alignment, Format, Line, Speaker, Token, Word

DEFAULT_BASE_URL = "https://mora.junx.dev"
DEFAULT_TIMEOUT = 15.0
VERSION = "0.2.0"


class MoraError(RuntimeError):
    """서버가 요청을 받아들이지 않았다."""

    def __init__(self, code: str, status: int) -> None:
        super().__init__(f"{code} (HTTP {status})")
        self.code = code
        self.status = status


class NotAligned(MoraError):
    """이 곡에는 쓸 수 있는 타이밍이 없다.

    곡을 못 찾았을 때(404 `NOT_FOUND`)와 가사가 너무 달라 못 붙였을 때(200 인데 `tier`가
    `none`)가 모두 여기로 온다 — 부르는 쪽에서는 둘 다 「띄울 것이 없다」로 같기 때문이다.
    어느 쪽인지는 `code` 로 갈린다.
    """


class Ambiguous(MoraError):
    """가수·제목으로 물었는데 그 이름의 녹음이 여럿이다. ISRC 나 MBID 로 물어야 한다."""


def _identify(
    isrc: str | None, mbid: str | None, artist: str | None, title: str | None, duration_ms: int | None
) -> dict[str, Any]:
    """Build the identifier half of a request body.

    서버는 셋을 **배타적으로** 본다 — ISRC 가 있으면 나머지를 아예 읽지 않는다. 그러니 여기서도
    같은 순서로 하나만 싣는다.

    @param {str | None} isrc - The recording's ISRC.
    @param {str | None} mbid - Its MusicBrainz recording id.
    @param {str | None} artist - Performer name.
    @param {str | None} title - Song name.
    @param {int | None} duration_ms - How long the recording is; required with artist/title.
    @returns {dict} The identifier fields.
    @throws {ValueError} When nothing identifies the recording.
    """
    if isrc:
        return {"isrc": isrc}
    if mbid:
        return {"mbid": mbid}
    if artist and title and duration_ms is not None:
        return {"artist": artist, "title": title, "duration_ms": int(duration_ms)}
    raise ValueError("곡을 가리키려면 isrc, mbid, 또는 artist·title·duration_ms 가 필요하다")


class Mora:
    """Mora 공개 API 를 부르는 클라이언트.

    맞춰 둔 타이밍은 서버가 들고 있고, 부르는 쪽은 **자기가 가진 가사를 그대로** 보낸다. 줄바꿈이
    다르거나 괄호 표기가 달라도 서버가 지문으로 견주어 제 자리에 얹어 주므로, 가사를 서버의 표기에
    맞출 필요가 없다. 얼마나 맞았는지는 `confidence` 로 돌아온다.

    @example
        mora = Mora()
        got = mora.align(lyrics, artist="검정치마", title="EVERYTHING", duration_ms=293000)
        for line in got:
            print(line.start_ms, line.text)
    """

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        user_agent: str = f"mora-lyrics-python/{VERSION}",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.user_agent = user_agent

    # ── 부르는 자리 ───────────────────────────────────────────────────────

    def align(
        self,
        text: str,
        *,
        isrc: str | None = None,
        mbid: str | None = None,
        artist: str | None = None,
        title: str | None = None,
        duration_ms: int | None = None,
        language: str | None = None,
        speakers: bool | Literal["auto"] = "auto",
    ) -> Alignment:
        """가사에 시각을 붙여 돌려준다.

        곡은 ISRC, MusicBrainz id, 또는 (artist, title, duration_ms) 셋 중 하나로 가리킨다.
        길이로도 견주므로 artist·title 만으로는 부족하다 — 같은 이름의 다른 녹음이 있다.

        **화자(`speakers`)**: 서버가 주는 화자 표는 **토큰 번호**로 키를 걸지만 `/v1/align` 의
        `spans` 는 시각이 붙은 것만 담아 오므로 배열 자리와 토큰 번호가 어긋난다. 그래서 토큰
        자리를 `/v1/tokenize` 로 한 번 더 받아 맞춘다. `"auto"`(기본)는 **화자 표가 실제로 올 때만**
        그 요청을 한다 — 한 사람이 부른 곡에서는 요청이 늘지 않는다. `False` 면 절대 안 부르고
        `Word.speaker` 는 모두 None 이 된다.

        @param {str} text - 가진 가사 전문.
        @param {str | None} isrc - 녹음의 ISRC.
        @param {str | None} mbid - MusicBrainz 녹음 id.
        @param {str | None} artist - 가수 이름.
        @param {str | None} title - 곡 이름.
        @param {int | None} duration_ms - 녹음 길이(ms). artist·title 로 물을 때 필수.
        @param {str | None} language - 가사 언어(BCP-47). 토크나이저가 참고한다.
        @param {bool | str} speakers - 화자를 붙일지. `"auto"` · True · False.
        @returns {Alignment} 시각이 붙은 가사.
        @throws {NotAligned} 곡을 못 찾았거나 붙일 만큼 닮지 않았을 때.
        @throws {Ambiguous} 가수·제목이 여러 녹음에 걸릴 때.
        @throws {MoraError} 그 밖에 서버가 거절했을 때.
        """
        body = _identify(isrc, mbid, artist, title, duration_ms)
        body["text"] = text
        if language is not None:
            body["language"] = language
        payload = self._post("/v1/align", body)
        if payload.get("tier") == "none":
            raise NotAligned("NO_ALIGNMENT", 200)

        tokens: list[Token] | None = None
        wants = bool(payload.get("word_speakers") or payload.get("line_speakers"))
        if speakers is True or (speakers == "auto" and wants):
            # 토크나이저는 서버가 고른 것을 그대로 따라야 한다 — 다른 것으로 자르면 번호가 어긋난다.
            tokens = self.tokenize(text, tokenizer=str(payload.get("tokenizer") or ""), language=language)
        return _parse(payload, text, tokens)

    def align_as(
        self,
        text: str,
        fmt: Format,
        *,
        isrc: str | None = None,
        mbid: str | None = None,
        artist: str | None = None,
        title: str | None = None,
        duration_ms: int | None = None,
        language: str | None = None,
    ) -> str:
        """서버가 직접 적어 주는 형식으로 받는다.

        **주의**: `lrc-a2` · `webvtt` · `ttml` · `lyricsfile` 은 **가사 글자를 담지 않는다** —
        큐 안에 코드포인트 범위 숫자가 들어간다. 그 글만으로는 재생기에 띄울 수 없다. 사람이 읽을
        자막이 필요하면 `align()` 뒤에 `to_lrc()` · `to_srt()` · `to_vtt()` 를 쓴다.

        @param {str} text - 가진 가사 전문.
        @param {str} fmt - `lrc-a2` · `lyricsfile` · `ttml` · `webvtt` 중 하나.
        @returns {str} 서버가 적어 준 글.
        """
        body = _identify(isrc, mbid, artist, title, duration_ms)
        body["text"] = text
        if language is not None:
            body["language"] = language
        return self._post(f"/v1/align?format={urllib.parse.quote(fmt)}", body, raw=True)

    def tokenize(self, text: str, *, tokenizer: str = "", language: str | None = None) -> list[Token]:
        """가사를 서버와 같은 방식으로 잘라 본다.

        @param {str} text - 자를 가사.
        @param {str} tokenizer - `unilab-v1` 또는 `unilab-v2`. 비우면 서버 기본값(v2).
        @param {str | None} language - 가사 언어. v2 가 참고한다.
        @returns {list[Token]} 토큰마다 글자·코드포인트 자리·줄 번호.
        """
        body: dict[str, Any] = {"text": text}
        if tokenizer:
            body["tokenizer"] = tokenizer
        if language is not None:
            body["language"] = language
        payload = self._post("/v1/tokenize", body)
        return [
            Token(text=text[int(start):int(end)], start=int(start), end=int(end), line=int(line))
            for start, end, line in payload.get("tokens") or []
        ]

    def align_fingerprint(
        self,
        fingerprint: dict[str, list[list[int]]],
        *,
        isrc: str | None = None,
        mbid: str | None = None,
        artist: str | None = None,
        title: str | None = None,
        duration_ms: int | None = None,
    ) -> dict[str, Any]:
        """가사 글자 없이 **지문만** 보내 시각을 받는다.

        글자를 서버에 보내고 싶지 않을 때 쓴다. 돌아오는 `spans`·`lines` 는 오프셋이 아니라
        **토큰 번호·줄 번호**라서, 화자 표와 자리가 그대로 맞는다.

        @param {dict} fingerprint - `{"lens": [[...]], "types": [[...]]}`.
        @returns {dict} 서버가 준 그대로. `spans` 는 `[토큰번호, 시작ms, 끝ms, 짐작]`.
        """
        body = _identify(isrc, mbid, artist, title, duration_ms)
        body["fingerprint"] = fingerprint
        return self._post("/v1/align/fingerprint", body)

    def lyrics(self, title: str, artist: str | None = None, *, providers=None, timeout: float | None = None):
        """가사 글을 제공처에서 가져온다 — Mora 는 타이밍만 주기 때문이다.

        bugs · flo · genie · vibe 에 차례로 물어 **처음 받은 것**을 돌려준다. 여럿을
        견주고 싶으면 `fetch_lyrics()` 를 직접 쓴다.

        @param {str} title - 곡 이름.
        @param {str | None} artist - 가수 이름. 같은 제목의 다른 곡을 가려낸다.
        @param {Sequence[str] | None} providers - 물어볼 곳. 비우면 다섯 곳 모두.
        @param {float | None} timeout - 한 요청이 기다릴 초. 비우면 이 클라이언트의 값.
        @returns {Lyrics | None} 받은 가사. 아무 곳도 못 주면 None.
        """
        from .sources import fetch_lyrics
        got = fetch_lyrics(title, artist, providers=providers,
                           timeout=self.timeout if timeout is None else timeout, first=True)
        return got[0] if got else None

    def health(self) -> bool:
        """@returns {bool} 서버가 살아 있는가."""
        try:
            self._request("GET", "/health", None)
        except MoraError:
            return False
        return True

    # ── 안쪽 ──────────────────────────────────────────────────────────────

    def _post(self, path: str, body: dict[str, Any], *, raw: bool = False) -> Any:
        text = self._request("POST", path, json.dumps(body, ensure_ascii=False).encode("utf-8"))
        return text if raw else json.loads(text)

    def _request(self, method: str, path: str, data: bytes | None) -> str:
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            method=method,
            headers={"User-Agent": self.user_agent, **({"Content-Type": "application/json"} if data else {})},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            raw = error.read().decode("utf-8", "replace")
            code = "UNKNOWN"
            try:
                code = str(json.loads(raw).get("error", code))
            except Exception:
                pass
            # 곡을 못 찾은 것, 이름이 겹치는 것, 서버가 고장난 것은 부르는 쪽에서 다르게 다뤄야 한다.
            failure = NotAligned if error.code == 404 else Ambiguous if error.code == 409 else MoraError
            raise failure(code, error.code) from None
        except urllib.error.URLError as error:
            raise MoraError(f"UNREACHABLE: {error.reason}", 0) from None


def _parse(payload: dict[str, Any], text: str, tokens: list[Token] | None) -> Alignment:
    """Turn the server's number arrays into words and lines.

    오프셋은 코드포인트 단위다. 파이썬 문자열도 코드포인트로 세므로 그대로 잘라도 맞는다.

    화자를 붙이려면 토큰 번호가 필요한데 `spans` 에는 없다 — `tokens` 를 받아 왔을 때만 자리로
    번호를 되찾아 붙인다. 안 받아 왔으면 `speaker` 는 None 으로 둔다. **잘못 붙이느니 비워 둔다.**

    @param {dict} payload - What `/v1/align` answered.
    @param {str} text - The lyric text that was sent.
    @param {list[Token] | None} tokens - The same text tokenized, when speakers are wanted.
    @returns {Alignment} The parsed alignment.
    """
    word_speaker = {int(index): int(who) for index, who, _ in payload.get("word_speakers") or []}
    line_speaker = {int(index): int(who) for index, who, _ in payload.get("line_speakers") or []}
    #: 토큰의 시작 자리 → 토큰 번호. `spans` 는 토큰의 자리를 그대로 옮겨 오므로 이것으로 맞는다.
    number_of = {one.start: index for index, one in enumerate(tokens or [])}
    line_of = {index: one.line for index, one in enumerate(tokens or [])}

    words: list[Word] = []
    for span in payload.get("spans") or []:
        start, end, start_ms, end_ms = int(span[0]), int(span[1]), int(span[2]), int(span[3])
        token = number_of.get(start)
        words.append(
            Word(
                text=text[start:end],
                start_ms=start_ms,
                end_ms=end_ms,
                interpolated=bool(span[4]) if len(span) > 4 else False,
                start=start,
                end=end,
                token=token,
                speaker=None if token is None else word_speaker.get(token),
            )
        )

    #: 줄 번호도 배열 자리가 아니다. 토큰을 받아 왔으면 그 줄 번호로, 아니면 비워 둔다.
    lines: list[Line] = []
    for row in payload.get("lines") or []:
        start, end, start_ms, end_ms = int(row[0]), int(row[1]), int(row[2]), int(row[3])
        held = tuple(one for one in words if one.start >= start and one.end <= end)
        number = next((line_of[one.token] for one in held if one.token is not None and one.token in line_of), None)
        lines.append(
            Line(
                text=text[start:end],
                start_ms=start_ms,
                end_ms=end_ms,
                words=held,
                start=start,
                end=end,
                index=number,
                speaker=None if number is None else line_speaker.get(number),
            )
        )

    return Alignment(
        tier=payload.get("tier", "none"),
        confidence=float(payload.get("confidence", 0.0)),
        tokenizer=str(payload.get("tokenizer", "")),
        alignment_id=int(payload.get("alignment_id", 0)),
        lines=tuple(lines),
        words=tuple(words),
        speakers=tuple(
            Speaker(int(a), int(b), int(c), float(d)) for a, b, c, d in (payload.get("speaker_turns") or [])
        ),
        text=text,
    )
