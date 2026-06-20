"""Tests for the dependency-safe pipeline-prefix runner used by the WS server.

The pipeline modules share a Context and are linearly dependent (transcript
needs fetch_info, summarize needs transcript, …), so the WS tool runs the
procOrder PREFIX up to and including the chosen target stage — never an
arbitrary subset.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from ytdrill.modules.base import Context        # noqa: E402
from ytdrill.stagerun import run_to             # noqa: E402


def _registry(order, calls):
    """A registry of fake modules that just record that they ran."""
    reg = {}
    for n in order:
        def run(self, ctx, _n=n):
            calls.append(_n)
        reg[n] = type(f"M_{n}", (), {"__init__": lambda self, cfg: None,
                                     "name": n, "run": run})
    return reg


def test_run_to_runs_prefix_up_to_target(tmp_path):
    order = ["fetch_info", "transcript", "summarize", "emit_tiddler"]
    calls = []
    reg = _registry(order, calls)
    ctx = Context(url="u", workdir=tmp_path, config={"modules": {}})

    stages = run_to(ctx, reg, target="summarize", order=order)

    assert calls == ["fetch_info", "transcript", "summarize"]
    assert [s["node"] for s in stages] == ["fetch_info", "transcript", "summarize"]
    assert all(isinstance(s["cost_ms"], float) for s in stages)


def test_run_to_unknown_target_raises(tmp_path):
    order = ["fetch_info", "transcript"]
    ctx = Context(url="u", workdir=tmp_path, config={"modules": {}})
    try:
        run_to(ctx, _registry(order, []), target="nope", order=order)
    except ValueError:
        return
    raise AssertionError("expected ValueError for unknown target stage")
