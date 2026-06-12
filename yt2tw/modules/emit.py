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

from .base import BaseModule, Context, bibkey_of

log = logging.getLogger("yt2tw")


def tw_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")[:17]


def b2(text: str) -> str:
    return hashlib.blake2b(text.encode("utf-8"), digest_size=16).hexdigest()


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

        tiddler = {
            "title": title,
            "caption": ctx.title,
            "created": now,
            "modified": now,
            "type": self.cfg.get("text_type", "text/markdown"),
            "tags": " ".join(f"[[{t}]]" for t in tags),
            "text": ctx.summary or ctx.transcript or ctx.description,

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

        ctx.tiddlers = [tiddler]
        out = ctx.workdir / f"{title}.json"
        out.write_text(json.dumps(ctx.tiddlers, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        ctx.output_path = out
        log.info("    tiddler -> %s", out)
