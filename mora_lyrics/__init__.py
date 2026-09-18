"""Mora — 노래의 어느 순간에 어느 낱말이 불리는지.

가진 가사를 그대로 보내면 시각이 붙어 돌아온다. 가사 표기를 서버에 맞출 필요는 없다 — 서버가
지문으로 견주어 제 자리에 얹는다.

@example
    from mora_lyrics import Mora

    mora = Mora()
    got = mora.align(lyrics, artist="검정치마", title="EVERYTHING", duration_ms=293000)
    print(got.tier, got.confidence)
    for line in got:
        print(line.start_ms, line.text)
"""

from .client import DEFAULT_BASE_URL, VERSION, Ambiguous, Mora, MoraError, NotAligned
from .export import to_lrc, to_srt, to_vtt
from .models import Alignment, Format, Line, Speaker, Tier, Token, Word
from .playback import Moment, Playhead
from .sources import PROVIDERS, Lyrics, LyricLine, fetch_lyrics, suggest

__version__ = VERSION

__all__ = [
    "Alignment",
    "Ambiguous",
    "DEFAULT_BASE_URL",
    "Format",
    "Line",
    "LyricLine",
    "Lyrics",
    "Moment",
    "Mora",
    "MoraError",
    "NotAligned",
    "PROVIDERS",
    "Playhead",
    "Speaker",
    "Tier",
    "Token",
    "Word",
    "__version__",
    "fetch_lyrics",
    "suggest",
    "to_lrc",
    "to_srt",
    "to_vtt",
]
