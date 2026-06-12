"""yt2tw CLI.

Usage:
    python -m yt2tw <youtube-url> [--workdir DIR] [--config config.json]
                    [--no-summary] [--media]

Exit codes: 0 ok, 1 runtime failure, 2 config error.
"""
from __future__ import annotations

import argparse
import logging
import sys
import tempfile
from pathlib import Path

from .env import load_env
from .modules.base import Context, load_config, run_pipeline
from .modules.yt import FetchInfo, Transcript, MediaDownload
from .modules.references import ExtractReferences
from .modules.slides import SlideExtract
from .modules.summarize import Summarize
from .modules.emit import EmitTiddler

REGISTRY = {
    "fetch_info": FetchInfo,
    "transcript": Transcript,
    "media": MediaDownload,
    "slides": SlideExtract,
    "summarize": Summarize,
    "extract_references": ExtractReferences,
    "emit_tiddler": EmitTiddler,
}

DEFAULT_CONFIG = {
    "procOrder": ["fetch_info", "transcript", "summarize",
                  "extract_references", "emit_tiddler"],
    "modules": {
        "summarize": {"model": "sonar", "max_tokens": 4096,
                      "temperature": 0.2,
                      "secret_cmd": ""},
        "emit_tiddler": {"tiddler_type": "video",
                         "text_type": "text/markdown"},
    },
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="yt2tw")
    ap.add_argument("url")
    ap.add_argument("--workdir", default=None,
                    help="working directory (default: temp dir, NOT ~/Downloads)")
    ap.add_argument("--config", default=None, help="path to config.json")
    ap.add_argument("--env", default=None,
                    help=".env file with PERPLEXITY_API_KEY (default search: "
                         "workdir, project root, cwd)")
    ap.add_argument("--no-summary", action="store_true",
                    help="skip Perplexity; tiddler text falls back to transcript")
    ap.add_argument("--media", action="store_true",
                    help="also download video + original audio (slide isolation prep)")
    ap.add_argument("--slides", action="store_true",
                    help="extract slide frames to a searchable OCR'd PDF "
                         "for pdfdrill (implies --media)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(message)s")

    if args.config:
        config = load_config(Path(args.config))
    else:
        default = Path(__file__).parents[1] / "config.json"
        config = load_config(default) if default.is_file() else dict(DEFAULT_CONFIG)

    proc = list(config.get("procOrder", DEFAULT_CONFIG["procOrder"]))
    if args.no_summary:
        proc = [m for m in proc if m not in ("summarize", "extract_references")]
    if (args.media or args.slides) and "media" not in proc:
        proc.insert(proc.index("emit_tiddler") if "emit_tiddler" in proc else len(proc),
                    "media")
    if args.slides and "slides" not in proc:
        proc.insert(proc.index("media") + 1, "slides")
    config["procOrder"] = proc

    workdir = Path(args.workdir) if args.workdir \
        else Path(tempfile.mkdtemp(prefix="yt2tw."))
    workdir.mkdir(parents=True, exist_ok=True)

    load_env(Path(args.env) if args.env else None,
             search=[workdir, Path(__file__).parents[1], Path.cwd()])

    ctx = Context(url=args.url, workdir=workdir, config=config)
    run_pipeline(ctx, REGISTRY)

    if ctx.output_path:
        print(ctx.output_path)
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
