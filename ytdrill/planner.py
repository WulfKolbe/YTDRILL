"""Lazy, layer-wise acquisition planner — the YTDRILL state machine.

Mirrors PDFDRILL's fact-gated planner philosophy: escalate only as far as a
goal needs, cheapest layer first, and NEVER run an expensive layer eagerly.

    info  →  transcript (captions)  →  audio (iff no transcript)  →  video (last resort)

  * `fetch_info`  — info block, size, description. Always, no download.
  * `transcript`  — original-language captions. Free; the normal content source.
  * `audio`       — download the ORIGINAL audio stream ONLY, and only when the
                    video has no usable caption track (it can then be
                    transcribed). Never pulls the video stream.
  * `video`       — the LAST RESORT: downloaded only for slide extraction.

This replaces the old blind prefix runner (``stagerun.run_to``), which ran
whatever stages were in ``procOrder`` — so a ``--slides`` order (or the
sandbox's target) downloaded the video unconditionally. Here the video stream
is reached only when the goal is ``slides``; a plain transcript/summary run can
never download it. (Designed for sandbox/remote runs, not the local-only
always-download assumption of the original ``yt2tw.sh``.)

Stdlib only; no network here, so the escalation logic is unit-testable.
"""
from __future__ import annotations

import time
from typing import Callable

from .modules.base import Context
from .stagerun import _detail

# goals that are only useful with a transcript (so an audio fallback makes sense)
_NEEDS_TRANSCRIPT = {"transcript", "summary", "tiddler"}

# base stages per goal; media-acquisition layers (audio/video) are inserted
# LAZILY right after `transcript`, never listed here.
GOAL_STAGES: dict[str, list[str]] = {
    "info":       ["fetch_info"],
    "transcript": ["fetch_info", "transcript", "emit_tiddler"],
    "summary":    ["fetch_info", "transcript", "summarize",
                   "extract_references", "emit_tiddler"],
    "tiddler":    ["fetch_info", "transcript", "summarize",
                   "extract_references", "emit_tiddler"],
    "slides":     ["fetch_info", "transcript", "slides", "emit_tiddler"],
}


def media_layers(goal: str, *, has_transcript: bool) -> list[str]:
    """The media-download layers to run AFTER ``fetch_info``+``transcript``,
    given whether captions were obtained. A subset of ``["audio", "video"]``,
    cheapest first, never escalating past what ``goal`` actually needs.

    The hard guarantee: ``"video"`` is returned ONLY for ``goal == "slides"`` —
    so a transcript/summary run can never download the video stream.
    """
    layers: list[str] = []
    if goal in _NEEDS_TRANSCRIPT and not has_transcript:
        layers.append("audio")        # fallback source for ASR — NOT the video
    if goal == "slides":
        layers.append("video")        # last resort: slide OCR needs the frames
    return layers


def run_goal(ctx: Context, registry: dict[str, type], *, goal: str,
             on_event: Callable[[str, dict], None] | None = None) -> list[dict]:
    """Run the lazy escalation for ``goal`` and return stage records
    ``[{node, cost_ms, detail}]``. Always runs ``fetch_info`` (+ ``transcript``
    when the goal needs it), then inserts the media layers chosen by
    :func:`media_layers` from the RUNTIME transcript state, then the goal's
    remaining stages. ``on_event(kind, payload)`` brackets each stage.
    """
    if goal not in GOAL_STAGES:
        raise ValueError(f"unknown goal {goal!r}; known: {', '.join(GOAL_STAGES)}")
    base = GOAL_STAGES[goal]
    stages: list[dict] = []

    def run_one(node: str) -> None:
        cls = registry.get(node)
        if cls is None:
            raise ValueError(f"no module registered for stage {node!r}")
        if on_event:
            on_event("start", {"node": node})
        t0 = time.perf_counter()
        cls(ctx.config).run(ctx)
        rec = {"node": node, "cost_ms": (time.perf_counter() - t0) * 1000.0,
               "detail": _detail(ctx, node)}
        stages.append(rec)
        if on_event:
            on_event("done", rec)

    run_one("fetch_info")                       # always: cheap metadata, no download
    if "transcript" in base:
        run_one("transcript")                   # free captions, the normal source
    for node in media_layers(goal, has_transcript=bool(ctx.transcript)):
        run_one(node)                           # lazy: audio iff no transcript / video iff slides
    for node in base:                           # the rest of the goal's stages
        if node in ("fetch_info", "transcript"):
            continue
        run_one(node)
    return stages
