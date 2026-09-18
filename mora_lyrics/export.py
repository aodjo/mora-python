"""시각이 붙은 가사를 자막 글로 적는다.

서버도 `lrc-a2` · `webvtt` · `ttml` 을 내주지만 **그 글에는 가사 글자가 없다** — 큐 안에 들어가는
것은 코드포인트 범위 숫자다. 재생기에 띄우려면 결국 글자가 필요하므로 여기서 조립한다.
"""

from __future__ import annotations

from .models import Alignment


def _lrc_time(ms: int) -> str:
    """Write a moment the way LRC does: `mm:ss.xx`.

    한 시간이 넘는 녹음에서도 분이 60 을 넘어 이어진다 — LRC 에는 시(hour) 칸이 없다.

    @param {int} ms - The moment.
    @returns {str} `mm:ss.xx`.
    """
    ms = max(0, ms)
    return f"{ms // 60000:02d}:{ms % 60000 // 1000:02d}.{ms % 1000 // 10:02d}"


def _clock(ms: int, comma: bool) -> str:
    """Write a moment the way SRT and WebVTT do: `hh:mm:ss,mmm` or `hh:mm:ss.mmm`.

    @param {int} ms - The moment.
    @param {bool} comma - True for SRT's comma, False for WebVTT's dot.
    @returns {str} The stamp.
    """
    ms = max(0, ms)
    mark = "," if comma else "."
    return f"{ms // 3600000:02d}:{ms % 3600000 // 60000:02d}:{ms % 60000 // 1000:02d}{mark}{ms % 1000:03d}"


def to_lrc(alignment: Alignment, *, enhanced: bool = True) -> str:
    """LRC 로 적는다.

    `enhanced` 면 낱말마다 `<mm:ss.xx>` 를 앞에 붙인다(A2 확장) — 노래방처럼 낱말 단위로 칠할 수
    있다. 낱말 시각이 없는 정렬에서는 그 표시가 없으므로 줄만 적는다.

    @param {Alignment} alignment - 시각이 붙은 가사.
    @param {bool} enhanced - 낱말 시각도 적을지.
    @returns {str} LRC 글.
    """
    out: list[str] = []
    for line in alignment.lines:
        if enhanced and line.words:
            body = "".join(f"<{_lrc_time(one.start_ms)}>{one.text}" for one in line.words)
        else:
            body = line.text
        out.append(f"[{_lrc_time(line.start_ms)}]{body}")
    return "\n".join(out) + "\n" if out else ""


def to_srt(alignment: Alignment) -> str:
    """SRT 자막으로 적는다.

    @param {Alignment} alignment - 시각이 붙은 가사.
    @returns {str} SRT 글.
    """
    out: list[str] = []
    for number, line in enumerate(alignment.lines, start=1):
        out.append(str(number))
        out.append(f"{_clock(line.start_ms, True)} --> {_clock(line.end_ms, True)}")
        out.append(line.text)
        out.append("")
    return "\n".join(out)


def to_vtt(alignment: Alignment, *, enhanced: bool = False) -> str:
    """WebVTT 로 적는다.

    `enhanced` 면 낱말마다 `<00:00:12.340>` 를 끼워 넣는다 — 브라우저가 그것으로 지금 부르는
    낱말에 `::cue(...)` 를 걸 수 있다.

    @param {Alignment} alignment - 시각이 붙은 가사.
    @param {bool} enhanced - 낱말 시각도 끼울지.
    @returns {str} WebVTT 글.
    """
    out: list[str] = ["WEBVTT", ""]
    for line in alignment.lines:
        out.append(f"{_clock(line.start_ms, False)} --> {_clock(line.end_ms, False)}")
        if enhanced and line.words:
            out.append("".join(f"<{_clock(one.start_ms, False)}>{one.text}" for one in line.words))
        else:
            out.append(line.text)
        out.append("")
    return "\n".join(out)
