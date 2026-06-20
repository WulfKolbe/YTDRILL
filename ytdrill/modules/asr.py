"""WhisperASR — transcribe the downloaded audio when a video has no captions.

The lazy fallback's second half: when ``transcript`` found no caption track,
``audio`` downloads the original audio stream and this module runs Whisper
(faster-whisper / CTranslate2 — CPU-friendly, no torch) over it, filling
``ctx.transcript`` + ``ctx.segments`` exactly like the caption path so every
downstream stage (summarize, emit, drillsidecar) is none the wiser.

Config (``modules.asr``): ``model`` (default ``base``), ``device`` (``cpu``),
``compute_type`` (``int8``), ``language`` (default: the info-dict language, else
auto-detect).

Degrades gracefully: no audio, or no faster-whisper installed → a warning and a
no-op (the run still emits a tiddler, just without a transcript).
"""
from __future__ import annotations

import logging
from pathlib import Path

from .base import BaseModule, Context

log = logging.getLogger("ytdrill")


def _attr(seg, key):
    """faster-whisper yields Segment objects; tests pass dicts. Read either."""
    return seg[key] if isinstance(seg, dict) else getattr(seg, key)


def segments_from_whisper(segments) -> list[dict]:
    """Whisper segments (seconds) → ytdrill segments ``[{t0,t1,text}]`` (ms),
    text stripped — the same shape the caption cleaners produce."""
    out: list[dict] = []
    for s in segments:
        out.append({
            "t0": int(round(_attr(s, "start") * 1000)),
            "t1": int(round(_attr(s, "end") * 1000)),
            "text": (_attr(s, "text") or "").strip(),
        })
    return out


class WhisperASR(BaseModule):
    name = "asr"

    def run(self, ctx: Context) -> None:
        audio = getattr(ctx, "audio_path", None)
        if not audio or not Path(audio).is_file():
            log.warning("    no audio to transcribe — skipping ASR")
            return
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            log.error("    faster-whisper not installed "
                      "(pip install faster-whisper) — skipping ASR")
            return

        model_size = self.cfg.get("model", "base")
        device = self.cfg.get("device", "cpu")
        compute = self.cfg.get("compute_type", "int8")
        lang = self.cfg.get("language") or \
            (ctx.language.split("-")[0] if ctx.language else None)

        log.info("    whisper %s (%s/%s) transcribing %s …",
                 model_size, device, compute, Path(audio).name)
        model = WhisperModel(model_size, device=device, compute_type=compute)
        segments, info = model.transcribe(str(audio), language=lang)
        segs = segments_from_whisper(segments)      # generator → list (does the work)

        ctx.segments = segs
        ctx.transcript = " ".join(s["text"] for s in segs if s["text"])
        ctx.transcript_source = "whisper"
        if not ctx.language and getattr(info, "language", None):
            ctx.language = info.language
        (ctx.workdir / "cleaned_transcript.md").write_text(
            ctx.transcript, encoding="utf-8")
        log.info("    whisper: %d segments, %d chars (lang=%s)",
                 len(segs), len(ctx.transcript), ctx.language or "?")
