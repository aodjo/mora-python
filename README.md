# mora-lyrics

노래의 어느 순간에 어느 낱말이 불리는지 — [Mora](https://mora.junx.dev) 정렬 API 파이썬 클라이언트.

**가진 가사를 그대로 보내면 됩니다.** 줄바꿈이 다르거나 괄호 표기가 달라도 서버가 지문으로 견주어 제 자리에 얹습니다. 가사를 서버의 표기에 맞출 필요가 없고, 얼마나 맞았는지는 `confidence` 로 돌아옵니다.

의존성이 없습니다 — 표준 라이브러리만 씁니다.

```bash
pip install mora-lyrics
```

## 쓰기

```python
from mora_lyrics import Mora

mora = Mora()
got = mora.align(lyrics, artist="검정치마", title="EVERYTHING", duration_ms=293000)

print(got.tier, got.confidence)     # word 0.97
for line in got:
    print(line.start_ms, line.text)
```

곡은 셋 중 하나로 가리킵니다. **서버는 이 순서로 배타적으로** 봅니다 — ISRC 가 있으면 나머지는 아예 읽지 않습니다.

| | |
|---|---|
| `isrc="KRA401200001"` | 가장 정확합니다 |
| `mbid="…"` | MusicBrainz 녹음 id |
| `artist=`, `title=`, `duration_ms=` | **길이가 필수입니다** — 같은 이름의 다른 녹음이 있습니다 |

## 지금 부르는 줄

```python
from mora_lyrics import Playhead

head = Playhead(got)
while playing:
    now = head.at(player.position_ms)
    if now.line is not None:
        draw(now.line.text, now.word, now.progress)
```

`Playhead` 는 저번에 있던 자리에서 한두 칸만 봅니다 — 1초에 수십 번 물어도 값이 안 듭니다. 뒤로 감아도 맞습니다.

한 번만 물을 때는 `got.line_at(ms)` · `got.word_at(ms)` 로 충분합니다.

## 자막으로 내보내기

```python
from mora_lyrics import to_lrc, to_srt, to_vtt

open("song.lrc", "w").write(to_lrc(got))            # 낱말 시각까지(A2)
open("song.srt", "w").write(to_srt(got))
open("song.vtt", "w").write(to_vtt(got, enhanced=True))
```

> **서버의 `align_as()` 와 다릅니다.** 서버가 내주는 `lrc-a2` · `webvtt` · `ttml` · `lyricsfile` 은 **가사 글자를 담지 않습니다** — 큐 안에 코드포인트 범위 숫자가 들어갑니다. 재생기에 띄울 자막이 필요하면 위의 `to_lrc()` 쪽을 쓰세요.

## 누가 불렀나

듀엣이나 화음이 있는 곡에서는 낱말마다 화자가 붙습니다.

```python
got = mora.align(lyrics, isrc="…")
for word in got.words:
    print(word.text, word.speaker)      # 0, 1, …
```

서버의 화자 표는 **토큰 번호**로 키를 거는데 `/v1/align` 의 `spans` 는 시각이 붙은 것만 담아 오므로 배열 자리와 번호가 어긋납니다. 그래서 이 라이브러리는 토큰 자리를 `/v1/tokenize` 로 한 번 더 받아 맞춥니다.

- `speakers="auto"` (기본) — 화자 표가 **실제로 올 때만** 그 요청을 합니다. 한 사람이 부른 곡에서는 요청이 늘지 않습니다.
- `speakers=False` — 절대 안 부릅니다. `word.speaker` 는 모두 `None` 입니다.

토큰을 안 받아 왔으면 `speaker` 를 **비워 둡니다. 잘못 붙이느니 비워 둡니다.**

## 오류

```python
from mora_lyrics import NotAligned, Ambiguous, MoraError

try:
    got = mora.align(lyrics, artist="아이유", title="밤편지", duration_ms=254000)
except NotAligned:
    ...        # 곡이 없거나(404) 가사가 너무 달라 못 붙임(tier none)
except Ambiguous:
    ...        # 그 이름의 녹음이 여럿 — ISRC 로 물어야 함(409)
except MoraError as error:
    print(error.code, error.status)
```

## 그 밖에

```python
mora.tokenize(text)                 # 서버와 같은 방식으로 자른 토큰
mora.align_fingerprint(fp, isrc=…)  # 가사 글자를 안 보내고 지문만
mora.health()
```

`align_fingerprint` 는 글자를 서버에 보내고 싶지 않을 때 씁니다. 돌아오는 자리는 오프셋이 아니라 **토큰 번호·줄 번호**라서 화자 표와 그대로 맞습니다.

## 알아 둘 것

- 오프셋은 **코드포인트** 단위입니다. 파이썬 문자열도 코드포인트로 세므로 `text[start:end]` 가 그대로 맞습니다.
- `tier` 는 `word` · `word-approx` · `line` · `none` 입니다. `line` 이면 `words` 가 비어 있고 줄 시각만 있습니다.
- 공개 API 는 열쇠가 필요 없습니다.
- 가사 한 편은 100만 자, 요청 본문은 2 MiB, 토큰은 10만 개까지입니다.

## 함께

- [mora-js](https://github.com/aodjo/mora-js) — 같은 API 의 자바스크립트·타입스크립트 클라이언트
- [Mora](https://github.com/aodjo/Mora) — 서버와 정렬기

MIT
