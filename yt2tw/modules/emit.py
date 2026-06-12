"""EmitTiddler — write a TiddlyWiki JSON array containing one tiddler.

Conventions carried over from pdfdrill / bm2tw:
  - title follows $Bibkey_$type_$serial; bibkey for YouTube is
    'yt<video_id>' (video IDs are already globally unique, URL-safe and
    stable — they ARE the natural content address for this source).
  - blake2b digests recorded for transcript and summary so a re-run can
    detect drift (re-uploaded captions, changed description) without
    diffing text fields.
  - timestamps in TW UTC format YYYYMMDDHHMMSSmmm.
  - the JSON array form is what TW5 auto-imports from tiddlers/ in the
    sandbox build recipe, and what /recipes/default/tiddlers/<title> PUT
    expects field-wise via tw.py.

The summary markdown goes into `text` (type text/markdown by default);
the cleaned transcript and timed segments are kept as fields on the SAME
tiddler so step 1 stays a single-tiddler array. The segments field is a
JSON string — it is the bridge to the later beamer/slide-isolation stage
(slide intervals x transcript segments join).
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from .base import BaseModule, Context, bibkey_of

log = logging.getLogger("yt2tw")


def tw_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")[:17]


def b2(text: str) -> str:
    return hashlib.blake2b(text.encode("utf-8"), digest_size=16).hexdigest()


_HTML_STYLE = """\
<style>
  .video-container { position: relative; width: 100%;
    padding-bottom: 56.25%; /* 16:9 */ height: 0; overflow: hidden; }
  .video-container video, .video-container iframe { position: absolute;
    top: 0; left: 0; width: 100%; height: 100%; border-radius: 8px;
    border: 0; }
  .title { color: yellow; margin-bottom: 10px; }
  .video-wrapper { max-width: 100%; }
</style>"""


def build_video_html(title: str, *, youtube_id: str = "",
                     video_url: str = "") -> str:
    """HTML body for the video tiddler: YouTube iframe embed when an id
    is given, plain <video> element for a local/file URL otherwise."""
    if youtube_id:
        player = (f'<iframe src="https://www.youtube.com/embed/{youtube_id}"'
                  f' allowfullscreen loading="lazy"></iframe>')
    else:
        player = (f'<video controls preload="metadata">'
                  f'<source src="{video_url}" type="video/mp4">'
                  f'Your browser does not support the video tag.</video>')
    return f"""{_HTML_STYLE}
<div class="video-wrapper">
  <h3 class="title">{title}</h3>
  <div class="video-container">{player}</div>
</div>"""


class EmitTiddler(BaseModule):
    name = "emit_tiddler"

    def run(self, ctx: Context) -> None:
        bibkey = bibkey_of(ctx)
        ttype = self.cfg.get("tiddler_type", "video")
        serial = int(self.cfg.get("serial", 1))
        title = f"{bibkey}_{ttype}_{serial:04d}"
        now = tw_now()

        tags = ["YouTube", "yt2tw"]
        if ctx.channel:
            tags.append(ctx.channel)
        tags += list(self.cfg.get("extra_tags", []))

        # companion HTML tiddler showing the video itself; the markdown
        # summary transcludes it and links the source
        html_title = f"{bibkey}_html_{serial:04d}"
        if ctx.url.startswith(("http://", "https://")):
            link = ctx.url
            html = build_video_html(ctx.title, youtube_id=ctx.video_id)
        else:
            src = (ctx.video_path or Path(ctx.url)).resolve()
            link = src.as_uri()
            html = build_video_html(ctx.title, video_url=link)

        links = [f"[{ctx.title}]({link})"]
        slides_pdf = (getattr(ctx, "extra_fields", {}) or {}).get("slides-pdf")
        if slides_pdf:
            links.append(
                f"[Slides PDF]({(ctx.workdir / slides_pdf).resolve().as_uri()})")
        body = ctx.summary or ctx.transcript or ctx.description
        text = f"{{{{{html_title}}}}}\n\n" + " · ".join(links) + f"\n\n{body}"

        tiddler = {
            "title": title,
            "caption": ctx.title,
            "created": now,
            "modified": now,
            "type": self.cfg.get("text_type", "text/markdown"),
            "tags": " ".join(f"[[{t}]]" for t in tags),
            "text": text,

            # provenance / identity
            "bibkey": bibkey,
            "url": ctx.url,
            "video-id": ctx.video_id,
            "channel": ctx.channel,
            "uploaded": ctx.upload_date,
            "duration": str(ctx.duration),
            "language": ctx.language,
            "transcript-source": ctx.transcript_source,
            "summary-model": ctx.summary_model,
            "transcript-blake2b": b2(ctx.transcript) if ctx.transcript else "",
            "summary-blake2b": b2(ctx.summary) if ctx.summary else "",

            # payload fields for later stages
            "description": ctx.description,
            "transcript": ctx.transcript,
            "segments": json.dumps(ctx.segments, ensure_ascii=False),
            "chapters": json.dumps(ctx.chapters, ensure_ascii=False),
            "yt-tags": ", ".join(ctx.tags),
        }
        # additive contract: downstream modules (extract_references, ...)
        # may attach extra tiddler fields without touching this class
        tiddler.update(getattr(ctx, "extra_fields", {}) or {})
        # drop empty optional fields to keep the tiddler tidy
        tiddler = {k: v for k, v in tiddler.items() if v != ""}

        html_tiddler = {
            "title": html_title,
            "caption": f"{ctx.title} (video)",
            "created": now,
            "modified": now,
            "type": "text/html",
            "tags": " ".join(f"[[{t}]]" for t in tags),
            "text": html,
            "bibkey": bibkey,
            "url": ctx.url,
            "video-id": ctx.video_id,
        }
        html_tiddler = {k: v for k, v in html_tiddler.items() if v != ""}

        ctx.tiddlers = [tiddler, html_tiddler]
        out = ctx.workdir / f"{title}.json"
        out.write_text(json.dumps(ctx.tiddlers, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        ctx.output_path = out
        log.info("    tiddler -> %s", out)
