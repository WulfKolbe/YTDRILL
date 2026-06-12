"""BaseModule / pipeline context for yt2tw.

Mirrors the pdfdrill convention: additive modules, config.json-driven
procOrder, shared mutable Context. Modules never modify each other's
classes; they only read/write Context attributes they own or declare.
"""
from __future__ import annotations

import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("yt2tw")


@dataclass
class Context:
    """Shared state passed through the pipeline."""
    url: str
    workdir: Path
    config: dict[str, Any]

    # populated by FetchInfo
    info: dict[str, Any] | None = None          # raw yt-dlp info dict
    video_id: str = ""
    title: str = ""
    channel: str = ""
    upload_date: str = ""                       # YYYYMMDD
    duration: int = 0                           # seconds
    language: str = ""                          # original language, BCP-47-ish
    description: str = ""
    tags: list[str] = field(default_factory=list)
    chapters: list[dict] = field(default_factory=list)
    comments: list[dict] = field(default_factory=list)   # opt-in (max_comments)

    # populated by Transcript
    transcript: str = ""                        # cleaned plain text
    segments: list[dict] = field(default_factory=list)  # [{t0,t1,text}] ms
    transcript_source: str = ""                 # "json3" | "srt" | ""

    # populated by Summarize
    summary: str = ""                           # markdown
    summary_model: str = ""

    # populated by MediaDownload (optional, for slide isolation later)
    video_path: Path | None = None
    audio_path: Path | None = None

    # populated by EmitTiddler
    tiddlers: list[dict] = field(default_factory=list)
    output_path: Path | None = None


class BaseModule:
    """A pipeline stage. Subclasses implement run(ctx)."""
    name: str = "base"

    def __init__(self, config: dict[str, Any]):
        # module-local config block: config["modules"][self.name]
        self.cfg: dict[str, Any] = config.get("modules", {}).get(self.name, {})

    def run(self, ctx: Context) -> None:  # pragma: no cover
        raise NotImplementedError


def load_config(path: Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def run_pipeline(ctx: Context, registry: dict[str, type[BaseModule]]) -> None:
    proc_order: list[str] = ctx.config.get("procOrder", [])
    for name in proc_order:
        cls = registry.get(name)
        if cls is None:
            log.error("unknown module in procOrder: %s", name)
            sys.exit(2)
        log.info("==> %s", name)
        cls(ctx.config).run(ctx)
