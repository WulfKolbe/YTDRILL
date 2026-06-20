"""Tests for the PDFDRILL-compatible sidecar emitter.

The YTDRILL WebSocket tool stores results as `<bibkey>.drill.json` + a
`<bibkey>.drill/` directory shaped exactly like pdfdrill's output, so the two
projects' artifacts are interchangeable. These tests pin that contract.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from ytdrill.modules.base import Context            # noqa: E402
from ytdrill.drillsidecar import emit_sidecar       # noqa: E402


def _ctx(tmp: Path) -> Context:
    ctx = Context(url="https://youtu.be/dQw4w9WgXcQ", workdir=tmp,
                  config={"modules": {}})
    ctx.video_id = "dQw4w9WgXcQ"
    ctx.title = "Test Video"
    ctx.channel = "Test Channel"
    ctx.duration = 212
    ctx.transcript = "hello world"
    ctx.segments = [{"t0": 0, "t1": 1000, "text": "hello world"}]
    ctx.summary = "# Summary"
    ctx.summary_model = "sonar"
    return ctx


def test_emit_sidecar_writes_pdfdrill_shaped_manifest(tmp_path):
    ctx = _ctx(tmp_path)
    path = emit_sidecar(ctx, stages=[
        {"node": "fetch_info", "cost_ms": 10.0, "detail": "ok"},
        {"node": "transcript", "cost_ms": 5.0, "detail": "1 seg"},
        {"node": "summarize", "cost_ms": 800.0, "detail": "sonar"},
    ])

    assert path == tmp_path / "ytdQw4w9WgXcQ.drill.json"
    d = json.loads(path.read_text(encoding="utf-8"))

    # PDFDRILL-compatible key set (nullable layer fields present for shape parity)
    for k in ("pdf", "facts", "evidence", "transitions",
              "pdfinfo", "bibtex", "urls", "dests", "layers"):
        assert k in d, f"missing PDFDRILL key: {k}"

    assert d["facts"] == ["INFO_FETCHED", "TRANSCRIPT_BUILT", "SUMMARIZED"]
    assert d["evidence"]["video_id"] == "dQw4w9WgXcQ"
    assert d["evidence"]["title"] == "Test Video"
    assert d["evidence"]["source_kind"] == "youtube"
    assert d["evidence"]["transcript_segments"] == 1
    assert d["evidence"]["summary_model"] == "sonar"


def test_transitions_chain_from_init(tmp_path):
    ctx = _ctx(tmp_path)
    path = emit_sidecar(ctx, stages=[
        {"node": "fetch_info", "cost_ms": 10.0, "detail": "ok"},
        {"node": "transcript", "cost_ms": 5.0, "detail": "1 seg"},
    ], now="2026-06-20T00:00:00Z")
    tr = json.loads(path.read_text(encoding="utf-8"))["transitions"]

    assert [t["node"] for t in tr] == ["fetch_info", "transcript"]
    assert tr[0]["from"] == "INIT" and tr[0]["to"] == "INFO_FETCHED"
    assert tr[1]["from"] == "INFO_FETCHED" and tr[1]["to"] == "TRANSCRIPT_BUILT"
    assert tr[0]["cost_ms"] == 10.0 and tr[0]["detail"] == "ok"
    assert tr[0]["ts"] == "2026-06-20T00:00:00Z"


def test_emit_writes_docmodel_dir(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.segments = [
        {"t0": 0, "t1": 1000, "text": "hello world"},
        {"t0": 1000, "t1": 2500, "text": "second segment"},
    ]
    emit_sidecar(ctx, stages=[{"node": "transcript", "detail": "2 seg"}])

    model = json.loads((tmp_path / "ytdQw4w9WgXcQ.drill" /
                        "model.docmodel.json").read_text(encoding="utf-8"))

    assert model["meta"]["bibkey"] == "ytdQw4w9WgXcQ"
    assert model["meta"]["source_path"] == ctx.url

    # one Document root + one Segment per transcript segment
    types = [o["type"] for o in model["objects"]]
    assert types.count("Document") == 1
    assert types.count("Segment") == 2
    root_id = model["meta"]["root_id"]
    assert any(o["id"] == root_id and o["type"] == "Document"
               for o in model["objects"])

    # alignments carry the segment timestamps (ms), in order
    assert len(model["alignments"]) == 2
    assert model["alignments"][0]["t0"] == 0
    assert model["alignments"][0]["t1"] == 1000
    assert model["alignments"][1]["text"] == "second segment"
