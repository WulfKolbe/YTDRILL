"""Lazy, layer-wise acquisition planner — the YTDRILL state machine.

Mirrors PDFDRILL's fact-gated philosophy: escalate only as far as the requested
artifacts need, cheapest layer first, and NEVER run an expensive layer eagerly.

    info  →  transcript (captions)  →  audio + ASR (iff no transcript)  →  video (slides only)

  * `fetch_info`  — info block, size, description. Always; no download.
  * `transcript`  — original-language captions. Free; the normal content source.
  * `audio` + `asr` — download the ORIGINAL audio stream ONLY and transcribe it
                    with Whisper, and only when the video has no usable caption
                    track. Never pulls the video stream.
  * `video`       — the LAST RESORT: downloaded only when slides are requested.

A local file takes the `local_source` path instead: the file already IS the
video (metadata via ffprobe, transcript from a sidecar `.srt`), so nothing is
ever downloaded.

This replaces the old blind prefix runner (``stagerun.run_to``), which ran
whatever stages sat in ``procOrder`` — so a ``--slides`` order downloaded the
video unconditionally. Here the video stream is reached only when slides are
requested; a plain transcript/summary run can never download it. (Designed for
sandbox/remote runs, not the local-only always-download assumption of the
original ``yt2tw.sh``.)

Stdlib only; no network here, so the escalation logic is unit-testable.
"""
from __future__ import annotations

import time
from typing import Callable

from .modules.base import Context
from .stagerun import _detail


def run_plan(ctx: Context, registry: dict[str, type], *, is_local: bool,
             want_summary: bool = True, want_slides: bool = False,
             want_media: bool = False, want_asr: bool = True,
             on_event: Callable[[str, dict], None] | None = None) -> list[dict]:
    """Run the lazy escalation and return stage records ``[{node, cost_ms,
    detail}]``. Composes the orthogonal options (summary × slides) and inserts
    the audio+ASR fallback only when captions are missing.

    Order:
      source   — ``local_source`` for a file, else ``fetch_info`` + ``transcript``
      fallback — ``audio`` then ``asr``, only for a URL whose transcript is empty
      summary  — ``summarize`` + ``extract_references`` when ``want_summary``
      video    — ``video`` download when slides/media are wanted (URL only;
                 a local file is already the video)
      slides   — ``slides`` when ``want_slides``
      emit     — ``emit_tiddler`` always

    ``want_asr=False`` disables the audio+Whisper fallback entirely: a URL with
    no captions then yields no transcript (and so no summary) rather than
    downloading and transcribing — the server's "summary from the first layers
    only, no Whisper" mode.

    ``on_event(kind, payload)`` brackets each stage with ``"start"``/``"done"``.
    """
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

    # -- source layer --
    if is_local:
        run_one("local_source")          # metadata + sidecar-srt transcript + video_path
    else:
        run_one("fetch_info")            # cheap metadata, no download
        run_one("transcript")            # free captions, the normal source
        if not ctx.transcript and want_asr:   # lazy fallback: download audio, then Whisper it
            run_one("audio")
            run_one("asr")

    # -- summary layer --
    if want_summary:
        run_one("summarize")
        run_one("extract_references")

    # -- video / slides layer (last resort; a local file is already the video) --
    if (want_slides or want_media) and not is_local:
        run_one("video")
    if want_slides:
        run_one("slides")

    run_one("emit_tiddler")
    return stages
