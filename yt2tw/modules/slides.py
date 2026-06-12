"""SlideExtract — additive module: lecture video -> searchable slides PDF.

Replaces the slideextract.com step. License-clean reimplementation of the
vid2slides idea (that repo is unlicensed and bit-rotted); everything heavy
is delegated to tools already required elsewhere in the pipeline:

    ffmpeg     scene-change candidate frames (select='gt(scene,thr)'),
               plus one frame per YouTube chapter mark when available
    ffmpeg     9x8 grayscale PGM thumbnails for perceptual hashing
    tesseract  per-frame OCR straight to searchable single-page PDFs
    gs         merges the pages into <bibkey>_slides.pdf

Near-duplicate frames (lecturer gesturing over a static slide, slide
revisits) are dropped with a difference hash (dHash): 9x8 grayscale,
bit = right neighbour brighter, hamming distance against ALL kept frames.

Outputs, via the additive ctx.extra_fields contract:
    slides-pdf    file name of the merged PDF (next to the tiddler JSON)
    slide-times   space-separated capture timestamps in seconds

The PDF is the pdfdrill handoff: pdfdrill turns it into slide tiddlers.
Requires ctx.video_path (the media module must run first; the CLI's
--slides flag arranges that).
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
from pathlib import Path

from .base import BaseModule, Context, bibkey_of

log = logging.getLogger("yt2tw")

_PTS = re.compile(r"pts_time:(\d+(?:\.\d+)?)")


# -- pure helpers (unit-tested) ---------------------------------------------
def parse_pgm(data: bytes) -> tuple[int, int, list[int]]:
    """Parse a binary (P5) PGM with maxval < 256 into (w, h, pixels)."""
    tokens: list[bytes] = []
    i = 0
    while len(tokens) < 4:                     # magic, width, height, maxval
        while i < len(data) and data[i:i + 1].isspace():
            i += 1
        if data[i:i + 1] == b"#":              # comment runs to end of line
            i = data.index(b"\n", i) + 1
            continue
        j = i
        while j < len(data) and not data[j:j + 1].isspace():
            j += 1
        tokens.append(data[i:j])
        i = j
    if tokens[0] != b"P5":
        raise ValueError(f"not a binary PGM: {tokens[0]!r}")
    w, h, maxval = int(tokens[1]), int(tokens[2]), int(tokens[3])
    if maxval > 255:
        raise ValueError("16-bit PGM unsupported")
    px = list(data[i + 1:i + 1 + w * h])       # single whitespace after maxval
    if len(px) != w * h:
        raise ValueError("truncated PGM")
    return w, h, px


def dhash(px: list[int], w: int, h: int) -> int:
    """Difference hash: bit set where the right neighbour is brighter."""
    bits = 0
    for row in range(h):
        for col in range(w - 1):
            bits = (bits << 1) | (px[row * w + col + 1] > px[row * w + col])
    return bits


def hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def dedupe(frames: list[tuple[str, int]], max_distance: int) -> list[str]:
    """Keep a frame only if it differs from EVERY kept frame (slide
    revisits later in the lecture stay dropped, not re-emitted)."""
    kept: list[str] = []
    hashes: list[int] = []
    for name, hsh in frames:
        if all(hamming(hsh, k) > max_distance for k in hashes):
            kept.append(name)
            hashes.append(hsh)
    return kept


# -- subprocess plumbing -----------------------------------------------------
def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=False, **kw)


class SlideExtract(BaseModule):
    name = "slides"

    def run(self, ctx: Context) -> None:
        if ctx.video_path is None or not Path(ctx.video_path).is_file():
            log.info("    no downloaded video (run with --slides/--media) "
                     "— skipping slide extraction")
            return
        for tool in ("ffmpeg", "tesseract", "gs"):
            if not shutil.which(tool):
                log.error("    %s not found — skipping slide extraction", tool)
                return

        thr = float(self.cfg.get("scene_threshold", 0.04))
        dist = int(self.cfg.get("hash_distance", 8))
        max_slides = int(self.cfg.get("max_slides", 200))
        lang = self.cfg.get("ocr_lang", "eng")

        frame_dir = ctx.workdir / "slide_frames"
        frame_dir.mkdir(exist_ok=True)

        times = self._extract_candidates(ctx, frame_dir, thr)
        frames = sorted(frame_dir.glob("*.png"))
        log.info("    %d candidate frames (scene>%s + %d chapters)",
                 len(frames), thr, len(ctx.chapters))
        if not frames:
            log.warning("    no candidate frames found")
            return

        hashed = []
        for f in frames:
            pgm = _run(["ffmpeg", "-v", "error", "-i", str(f),
                        "-vf", "scale=9:8:flags=area,format=gray",
                        "-f", "image2pipe", "-vcodec", "pgm", "-"]).stdout
            w, h, px = parse_pgm(pgm)
            hashed.append((f.name, dhash(px, w, h)))
        keep = dedupe(hashed, dist)[:max_slides]
        if len(keep) == max_slides:
            log.warning("    capped at max_slides=%d", max_slides)
        log.info("    %d unique slides after dedupe", len(keep))

        pdfs = []
        for name in keep:
            base = frame_dir / Path(name).stem
            r = _run(["tesseract", str(frame_dir / name), str(base),
                      "-l", lang, "pdf"])
            if r.returncode == 0 and base.with_suffix(".pdf").is_file():
                pdfs.append(base.with_suffix(".pdf"))
            else:
                log.warning("    OCR failed for %s: %s", name,
                            r.stderr.decode(errors="replace")[:200])
        if not pdfs:
            log.warning("    no OCR'd pages produced")
            return

        out = ctx.workdir / f"{bibkey_of(ctx)}_slides.pdf"
        r = _run(["gs", "-dBATCH", "-dNOPAUSE", "-q",
                  "-sDEVICE=pdfwrite", f"-sOutputFile={out}",
                  *map(str, pdfs)])
        if r.returncode != 0 or not out.is_file():
            log.error("    gs merge failed: %s",
                      r.stderr.decode(errors="replace")[:200])
            return

        kept_times = [times.get(n, -1.0) for n in keep]
        extra = getattr(ctx, "extra_fields", None) or {}
        extra["slides-pdf"] = out.name
        extra["slide-times"] = " ".join(
            str(int(t)) for t in kept_times if t >= 0)
        ctx.extra_fields = extra  # type: ignore[attr-defined]
        log.info("    slides -> %s (%d pages)", out, len(pdfs))

    # -- candidate frame extraction ------------------------------------
    def _extract_candidates(self, ctx: Context, frame_dir: Path,
                            thr: float) -> dict[str, float]:
        """Scene-change frames + one frame 2s after each chapter mark.
        Returns {file name: timestamp seconds}."""
        video = str(ctx.video_path)
        times: dict[str, float] = {}

        r = _run(["ffmpeg", "-v", "info", "-i", video,
                  "-vf", f"select='gt(scene,{thr})+eq(n,0)',showinfo",
                  "-vsync", "vfr", str(frame_dir / "scene_%05d.png")])
        pts = _PTS.findall(r.stderr.decode(errors="replace"))
        for idx, t in enumerate(pts, start=1):
            times[f"scene_{idx:05d}.png"] = float(t)

        # chapter starts are slide boundaries on edited lectures; +2s
        # skips the crossfade YouTube chapters often sit on
        for i, ch in enumerate(ctx.chapters or [], start=1):
            t = float(ch.get("start_time") or 0) + 2.0
            if t >= (ctx.duration or 1e9):
                continue
            name = f"chap_{i:05d}.png"
            _run(["ffmpeg", "-v", "error", "-ss", str(t), "-i", video,
                  "-frames:v", "1", str(frame_dir / name)])
            times[name] = t
        return times
