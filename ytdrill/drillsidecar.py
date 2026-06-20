"""Emit YTDRILL pipeline results as PDFDRILL-compatible artifacts.

PDFDRILL stores each drilled document as two things next to the source:

  * ``<name>.drill.json`` — a state manifest: ``facts`` (state-machine flags),
    ``evidence`` (flat metadata/counts), ``transitions`` (one per stage), plus
    nullable layer fields (``pdfinfo``/``bibtex``/``urls``/``layers`` …).
  * ``<name>.drill/`` — a directory with ``model.docmodel.json`` (the semantic
    graph) and per-layer artifacts.

YTDRILL writes the same shapes so the two projects' outputs are interchangeable
(the same readers, the drillui bridge, etc. work on both). Each pipeline stage
maps to a state fact; the transcript becomes the docmodel's content streams and
timestamp alignments.

Stdlib only.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .modules.base import Context, bibkey_of

YTDRILL_VERSION = "0.1.0"

# pipeline stage name -> state-machine fact it asserts on completion
_STAGE_FACT = {
    "fetch_info": "INFO_FETCHED",
    "transcript": "TRANSCRIPT_BUILT",
    "summarize": "SUMMARIZED",
    "extract_references": "REFERENCES_EXTRACTED",
    "media": "MEDIA_DOWNLOADED",
    "slides": "SLIDES_BUILT",
    "emit_tiddler": "TIDDLERS_BUILT",
}


def _source_kind(ctx: Context) -> str:
    return "local" if getattr(ctx, "video_path", None) and not ctx.video_id \
        else "youtube"


def _evidence(ctx: Context) -> dict:
    return {
        "source_kind": _source_kind(ctx),
        "video_id": ctx.video_id,
        "title": ctx.title,
        "channel": ctx.channel,
        "duration_s": ctx.duration,
        "language": ctx.language,
        "transcript_source": ctx.transcript_source,
        "transcript_chars": len(ctx.transcript or ""),
        "transcript_segments": len(ctx.segments or []),
        "summary_model": ctx.summary_model,
        "bibkey": bibkey_of(ctx),
    }


def _transitions(stages: list[dict], ts: str) -> list[dict]:
    """One transition per known stage, walking the state machine from INIT."""
    out, state = [], "INIT"
    for s in stages:
        fact = _STAGE_FACT.get(s["node"])
        if fact is None:
            continue
        out.append({
            "ts": ts,
            "node": s["node"],
            "from": state,
            "to": fact,
            "cost_ms": s.get("cost_ms"),
            "detail": s.get("detail", ""),
        })
        state = fact
    return out


def _docmodel(ctx: Context, bibkey: str) -> dict:
    """The semantic graph: a Document root plus one Segment object per transcript
    segment, with timestamp alignments — the substrate later compiler layers
    (claims, evidence, contradictions) attach to."""
    root_id = f"{bibkey}-doc"
    objects = [{
        "id": root_id, "type": "Document",
        "bibkey": bibkey, "title": ctx.title, "channel": ctx.channel,
    }]
    alignments = []
    for i, seg in enumerate(ctx.segments or [], start=1):
        sid = f"{bibkey}-seg-{i:04d}"
        objects.append({
            "id": sid, "type": "Segment", "parent": root_id,
            "text": seg.get("text", ""),
        })
        alignments.append({
            "object_id": sid,
            "t0": seg.get("t0"), "t1": seg.get("t1"),
            "text": seg.get("text", ""),
        })
    return {
        "meta": {
            "bibkey": bibkey,
            "source_path": ctx.url,
            "pages": [],
            "num_pages": 0,
            "root_id": root_id,
        },
        "streams": {"transcript_full": {"text": ctx.transcript or ""}},
        "objects": objects,
        "alignments": alignments,
    }


def emit_sidecar(ctx: Context, *, stages: list[dict],
                 version: str = YTDRILL_VERSION, now: str | None = None) -> Path:
    """Write ``<bibkey>.drill.json`` into ``ctx.workdir`` and return its path.

    ``stages`` is the ordered list of stages that ran, each a dict with at least
    a ``node`` (the pipeline stage name); ``cost_ms`` and ``detail`` are optional.
    ``now`` overrides the transition timestamp (ISO-8601 Z); defaults to UTC now.
    """
    ts = now or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    bibkey = bibkey_of(ctx)
    facts = [_STAGE_FACT[s["node"]] for s in stages if s["node"] in _STAGE_FACT]

    drill_dir = ctx.workdir / f"{bibkey}.drill"
    drill_dir.mkdir(parents=True, exist_ok=True)
    model_path = drill_dir / "model.docmodel.json"
    model_path.write_text(
        json.dumps(_docmodel(ctx, bibkey), indent=2, ensure_ascii=False),
        encoding="utf-8")

    evidence = _evidence(ctx)
    evidence["model_path"] = f"{bibkey}.drill/model.docmodel.json"
    manifest = {
        "pdf": ctx.url,
        "ytdrill_version": version,
        "facts": facts,
        "evidence": evidence,
        "pdfinfo": None,
        "bibtex": None,
        "urls": None,
        "dests": None,
        "layers": {},
        "transitions": _transitions(stages, ts),
    }
    path = ctx.workdir / f"{bibkey}.drill.json"
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False),
                    encoding="utf-8")
    return path
