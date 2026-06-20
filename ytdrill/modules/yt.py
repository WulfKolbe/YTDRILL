"""yt-dlp-backed modules: FetchInfo, Transcript, MediaDownload.

Design goal: ONE metadata extraction per video. The bash version called
yt-dlp three times (video, audio, subs+description), tripling exposure to
YouTube's bot detection. Here FetchInfo runs extract_info(download=False)
once and caches the info dict on the Context; every later module reads
from it.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from pathlib import Path

from .base import BaseModule, Context
from ..clean import clean_json3, clean_srt

log = logging.getLogger("ytdrill")


def _ydl(workdir: Path, **extra):
    import yt_dlp
    opts = {
        "quiet": True,
        "no_warnings": True,
        "retries": 5,
        "paths": {"home": str(workdir)},
        # keep yt-dlp's own network hygiene defaults; cookies can be added
        # via config["modules"]["fetch_info"]["cookiesfrombrowser"] later
        **extra,
    }
    return yt_dlp.YoutubeDL(opts)


# --------------------------------------------------------------------------
class FetchInfo(BaseModule):
    """Single extract_info() call; populates all metadata on the Context."""
    name = "fetch_info"

    def run(self, ctx: Context) -> None:
        opts = {}
        cookies = self.cfg.get("cookiesfrombrowser")
        if cookies:
            opts["cookiesfrombrowser"] = (cookies,)
        if self.cfg.get("nocheckcertificate"):   # only for MITM-proxied sandboxes
            opts["nocheckcertificate"] = True
        extractor_args: dict = {}
        client = self.cfg.get("player_client")   # e.g. "android" — bot-gate workaround
        if client:
            extractor_args.setdefault("youtube", {})["player_client"] = [client]
        max_comments = int(self.cfg.get("max_comments", 0))
        if max_comments:                          # opt-in: extra API surface
            opts["getcomments"] = True
            extractor_args.setdefault("youtube", {}).update({
                "max_comments": [str(max_comments), "all", "0", "0"],
                "comment_sort": ["top"]})
        if extractor_args:
            opts["extractor_args"] = extractor_args
        with _ydl(ctx.workdir, **opts) as ydl:
            info = ydl.extract_info(ctx.url, download=False)

        ctx.info = info
        ctx.video_id = info.get("id", "")
        ctx.title = info.get("title", "")
        ctx.channel = info.get("channel") or info.get("uploader", "")
        ctx.upload_date = info.get("upload_date", "")
        ctx.duration = int(info.get("duration") or 0)
        ctx.description = info.get("description", "") or ""
        ctx.tags = list(info.get("tags") or [])
        ctx.chapters = list(info.get("chapters") or [])
        ctx.language = info.get("language") or ""
        ctx.comments = [
            {"author": c.get("author", ""), "text": c.get("text", "")}
            for c in (info.get("comments") or [])
            if c.get("text")
        ]

        # persist for debugging / later pdfdrill reference-following
        (ctx.workdir / f"{ctx.video_id}.info.json").write_text(
            json.dumps(info, default=str), encoding="utf-8")
        log.info("    %s — %s (%ss, lang=%s)",
                 ctx.video_id, ctx.title, ctx.duration, ctx.language or "?")


# --------------------------------------------------------------------------
class Transcript(BaseModule):
    """Fetch the original-language auto captions from the cached info dict.

    Preference order for the caption track:
      1. manual subtitles in the original language
      2. automatic_captions '<lang>-orig' (YouTube's untranslated ASR track)
      3. automatic_captions '<lang>'
      4. first available manual track, then first auto track

    Preference order for the format: json3 (clean, timed) > srt/vtt fallback
    through the awk-port cleaner.
    """
    name = "transcript"

    def run(self, ctx: Context) -> None:
        info = ctx.info or {}
        subs = info.get("subtitles") or {}
        autos = info.get("automatic_captions") or {}
        lang = ctx.language or ""

        track, track_name = self._pick_track(subs, autos, lang)
        if not track:
            log.warning("    no caption track available")
            return

        fmt = self._pick_fmt(track)
        if not fmt:
            log.warning("    caption track %s has no usable format", track_name)
            return

        raw = self._download(fmt["url"])
        ext = fmt.get("ext", "")
        if ext == "json3":
            ctx.transcript, ctx.segments = clean_json3(raw)
            ctx.transcript_source = "json3"
        else:
            ctx.transcript, ctx.segments = clean_srt(raw)
            ctx.transcript_source = ext or "srt"

        (ctx.workdir / "cleaned_transcript.md").write_text(
            ctx.transcript, encoding="utf-8")
        log.info("    track=%s fmt=%s, %d segments, %d chars",
                 track_name, ext, len(ctx.segments), len(ctx.transcript))

    @staticmethod
    def _pick_track(subs: dict, autos: dict, lang: str):
        if lang:
            for key in (lang, lang.split("-")[0]):
                if key in subs:
                    return subs[key], f"sub:{key}"
            for key in (f"{lang}-orig", lang, lang.split("-")[0]):
                if key in autos:
                    return autos[key], f"auto:{key}"
        # original ASR track is suffixed -orig even when info.language is empty
        for key in autos:
            if key.endswith("-orig"):
                return autos[key], f"auto:{key}"
        if subs:
            key = next(iter(subs))
            return subs[key], f"sub:{key}"
        if autos:
            key = next(iter(autos))
            return autos[key], f"auto:{key}"
        return None, ""

    @staticmethod
    def _pick_fmt(track: list[dict]) -> dict | None:
        by_ext = {f.get("ext"): f for f in track}
        for ext in ("json3", "srt", "vtt"):
            if ext in by_ext:
                return by_ext[ext]
        return track[0] if track else None

    @staticmethod
    def _download(url: str, retries: int = 5) -> str:
        """GET with backoff — YouTube's timedtext endpoint 429s readily
        (the bash version used `--retries 5` for the same reason).
        YTDRILL_INSECURE_SSL=1 disables verification for MITM-proxied
        sandboxes only; never set it on a normal machine."""
        import ssl
        import time
        ctx_ssl = None
        if os.environ.get("YTDRILL_INSECURE_SSL"):
            ctx_ssl = ssl.create_default_context()
            ctx_ssl.check_hostname = False
            ctx_ssl.verify_mode = ssl.CERT_NONE
        last: Exception | None = None
        for attempt in range(retries):
            try:
                req = urllib.request.Request(
                    url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=60,
                                            context=ctx_ssl) as resp:
                    return resp.read().decode("utf-8", errors="replace")
            except urllib.error.HTTPError as e:
                last = e
                if e.code not in (429, 500, 502, 503):
                    raise
                wait = 2 ** attempt
                log.warning("    HTTP %s on captions, retry in %ss "
                            "(%d/%d)", e.code, wait, attempt + 1, retries)
                time.sleep(wait)
        raise last  # type: ignore[misc]


# --------------------------------------------------------------------------
class MediaDownload(BaseModule):
    """OPTIONAL stage (off by default in procOrder) for the future
    slide-isolation / beamer-recovery work.

    Audio selection: when a video carries multiple audio streams (dubs),
    yt-dlp assigns the ORIGINAL track a higher language_preference (>0,
    typically 10) and marks it 'original' in format_note / audio_is_default.
    Plain 'bestaudio' already sorts on language_preference, so the default
    selector picks the original stream — we additionally pin it explicitly
    so a future yt-dlp behaviour change cannot silently switch to a dub.
    """
    name = "media"

    def run(self, ctx: Context) -> None:
        info = ctx.info or {}
        fmt_audio = self._original_audio_selector(info)
        fmt = self.cfg.get("format") or f"bestvideo*+{fmt_audio}/best"
        outtmpl = self.cfg.get("outtmpl", "%(id)s.%(ext)s")
        with _ydl(ctx.workdir, format=fmt,
                  outtmpl={"default": outtmpl},
                  merge_output_format="mp4") as ydl:
            res = ydl.process_ie_result(dict(info), download=True)
        path = res.get("requested_downloads", [{}])[0].get("filepath") \
            or res.get("filepath")
        if path:
            ctx.video_path = Path(path)
            log.info("    media -> %s", path)

    @staticmethod
    def _original_audio_selector(info: dict) -> str:
        return _original_audio_selector(info)


# module-level so AudioDownload can share it without a MediaDownload instance
def _original_audio_selector(info: dict) -> str:
    """A yt-dlp format selector pinning the ORIGINAL audio track. When a video
    carries multiple audio streams (dubs), yt-dlp gives the original a higher
    language_preference (>0) and marks it 'original'; we pin it explicitly so a
    future yt-dlp default change can't silently pick a dub."""
    for f in info.get("formats", []):
        note = (f.get("format_note") or "").lower()
        if f.get("acodec") not in (None, "none") and (
            "original" in note or f.get("language_preference", -1) > 0
        ):
            lang = f.get("language")
            if lang:
                return f"bestaudio[language^={lang.split('-')[0]}]"
    return "bestaudio"


# --------------------------------------------------------------------------
class AudioDownload(BaseModule):
    """The lazy AUDIO-ONLY fallback: download the original audio stream when a
    video has no usable caption track, so it can later be transcribed (ASR).
    Never fetches the video stream — that is the slides-only last resort.
    """
    name = "audio"

    @staticmethod
    def format_for(info: dict, cfg: dict) -> str:
        """An audio-only yt-dlp selector (no video stream)."""
        return cfg.get("format") or _original_audio_selector(info)

    def run(self, ctx: Context) -> None:
        info = ctx.info or {}
        fmt = self.format_for(info, self.cfg)
        outtmpl = self.cfg.get("outtmpl", "%(id)s.%(ext)s")
        with _ydl(ctx.workdir, format=fmt,
                  outtmpl={"default": outtmpl}) as ydl:
            res = ydl.process_ie_result(dict(info), download=True)
        path = res.get("requested_downloads", [{}])[0].get("filepath") \
            or res.get("filepath")
        if path:
            ctx.audio_path = Path(path)
            log.info("    audio -> %s", path)
