"""Transcript cleaning.

Two paths:

1. json3 (preferred): YouTube's native timedtext format, fetched by yt-dlp.
   Events carry segment-level text with start time / duration in ms.
   No rolling-caption duplication, timestamps preserved -> usable later for
   beamer slide alignment.

2. SRT fallback: a faithful port of clean_transcript.awk — paragraph blocks,
   timestamp validation, consecutive-duplicate suppression. Timing is
   recovered per block (the awk script discarded it).
"""
from __future__ import annotations

import json
import re

# HH:MM:SS,mmm --> HH:MM:SS,mmm   (also tolerate '.' as VTT leftovers)
_TS_LINE = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[,.](\d{3})"
)
_WS = re.compile(r"\s+")


def _ts_ms(h: str, m: str, s: str, ms: str) -> int:
    return ((int(h) * 60 + int(m)) * 60 + int(s)) * 1000 + int(ms)


# --------------------------------------------------------------------------
# SRT path — port of clean_transcript.awk
# --------------------------------------------------------------------------
def clean_srt(srt_text: str) -> tuple[str, list[dict]]:
    """Return (plain_text, segments). Port of the awk logic:

    - records are blank-line-separated blocks (awk RS="")
    - field 2 must look like an SRT timestamp, else skip block
    - strip \r, trim, drop empty lines
    - suppress lines identical to the previously appended line
    """
    out_parts: list[str] = []
    segments: list[dict] = []
    prev = ""

    # awk paragraph mode (RS=""): records separated by runs of EMPTY lines.
    # Lines containing only \r or whitespace are CONTENT, not separators —
    # this matches gawk exactly (verified against the reference script).
    # Caveat shared with the original: files with CRLF endings throughout
    # have no truly empty lines; normalise those up front (awk would fail).
    if "\r\n" in srt_text and "\n\n" not in srt_text:
        srt_text = srt_text.replace("\r\n", "\n")
    blocks = re.split(r"\n{2,}", srt_text)
    for block in blocks:
        lines = block.split("\n")
        if len(lines) < 2:
            continue
        m = _TS_LINE.search(lines[1])
        if not m:
            continue
        t0 = _ts_ms(*m.groups()[0:4])
        t1 = _ts_ms(*m.groups()[4:8])

        block_lines: list[str] = []
        for line in lines[2:]:
            line = line.replace("\r", "").strip()
            if not line:
                continue
            if line != prev:                       # consecutive-dup suppression
                out_parts.append(line)
                block_lines.append(line)
                prev = line
        if block_lines:
            segments.append({"t0": t0, "t1": t1, "text": " ".join(block_lines)})

    return " ".join(out_parts), segments


# --------------------------------------------------------------------------
# json3 path — YouTube timedtext
# --------------------------------------------------------------------------
def clean_json3(json3_text: str) -> tuple[str, list[dict]]:
    """Parse YouTube json3 caption data into (plain_text, segments).

    Events with only a trailing "\n" segment are window-clear markers and
    are skipped. Segment text is concatenated per event; whitespace is
    normalised. Auto captions arrive non-overlapping here, so no dedup
    pass is needed — but we keep a cheap consecutive-dup guard anyway.
    """
    data = json.loads(json3_text)
    out_parts: list[str] = []
    segments: list[dict] = []
    prev = ""

    for ev in data.get("events", []):
        segs = ev.get("segs")
        if not segs:
            continue
        text = _WS.sub(" ", "".join(s.get("utf8", "") for s in segs)).strip()
        if not text:
            continue
        if text == prev:
            continue
        prev = text
        t0 = int(ev.get("tStartMs", 0))
        t1 = t0 + int(ev.get("dDurationMs", 0))
        out_parts.append(text)
        segments.append({"t0": t0, "t1": t1, "text": text})

    return " ".join(out_parts), segments
