"""LocalSource — additive module: local video file (4K Video Downloader+
exports, or anything else on disk) instead of a YouTube fetch.

Replaces fetch_info + transcript + media in one stage when the CLI is
given a file path instead of a URL:

    title        file stem (4KVD+ names files after the video title)
    bibkey       loc<blake2b(stem)[:11]>  (deterministic across re-runs;
                 EmitTiddler honours ctx.bibkey when set)
    duration     ffprobe
    transcript   sidecar SRT — 4KVD+ writes <stem>.<lang>.srt; language
                 picked via modules.local_source.lang_priority, cleaned
                 through the same clean_srt used for YouTube fallbacks
    video_path   the file itself, so --slides works unchanged

No sidecar SRT is not an error: the tiddler simply has no transcript
(summarize will be skipped by its own no-input guard).
"""
from __future__ import annotations

import hashlib
import logging
import shutil
import subprocess
from pathlib import Path

from .base import BaseModule, Context
from ..clean import clean_srt

log = logging.getLogger("ytdrill")


# -- pure helpers (unit-tested) ---------------------------------------------
def find_sidecar_subs(video: Path) -> dict[str, Path]:
    """{lang: path} for every <stem>[.<lang>].srt next to the video.
    The no-suffix form <stem>.srt maps to lang ''."""
    subs: dict[str, Path] = {}
    prefix = video.stem + "."
    for f in video.parent.glob("*.srt"):
        if not f.name.startswith(prefix):
            continue
        lang = f.name[len(prefix):-len(".srt")].rstrip(".")
        subs[lang] = f
    return subs


def pick_sub(subs: dict[str, Path], priority: list[str]) -> tuple[str, Path] | None:
    """First language in priority order, else any (sorted for determinism)."""
    if not subs:
        return None
    for lang in priority:
        if lang in subs:
            return lang, subs[lang]
    lang = sorted(subs)[0]
    return lang, subs[lang]


def local_id(stem: str) -> str:
    """Deterministic 11-char id (YouTube-id-sized) from the file stem."""
    return hashlib.blake2b(stem.encode("utf-8"), digest_size=16).hexdigest()[:11]


# ---------------------------------------------------------------------------
class LocalSource(BaseModule):
    name = "local_source"

    def run(self, ctx: Context) -> None:
        video = Path(ctx.url).expanduser().resolve()
        if not video.is_file():
            log.error("    local file not found: %s", video)
            raise SystemExit(1)

        ctx.video_path = video
        ctx.title = video.stem
        ctx.video_id = local_id(video.stem)
        ctx.bibkey = f"loc{ctx.video_id}"        # type: ignore[attr-defined]
        ctx.duration = self._probe_duration(video)
        log.info("    %s — %s (%ds)", ctx.bibkey, ctx.title, ctx.duration)

        priority = list(self.cfg.get("lang_priority", ["en", "de", ""]))
        picked = pick_sub(find_sidecar_subs(video), priority)
        if picked is None:
            log.warning("    no sidecar .srt found — no transcript")
            return
        lang, srt_path = picked
        text, segs = clean_srt(srt_path.read_text(encoding="utf-8-sig",
                                                  errors="replace"))
        ctx.transcript = text
        ctx.segments = segs
        ctx.transcript_source = "srt"
        ctx.language = lang
        log.info("    sidecar %s: %d segments, %d chars",
                 srt_path.name, len(segs), len(text))

    @staticmethod
    def _probe_duration(video: Path) -> int:
        if not shutil.which("ffprobe"):
            return 0
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(video)],
            capture_output=True, text=True, stdin=subprocess.DEVNULL)
        try:
            return int(float(r.stdout.strip()))
        except ValueError:
            return 0
