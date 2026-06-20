"""Run the pipeline up to a chosen target stage, recording per-stage timing.

The WS tool lets the user pick a *target* stage; because the modules are
linearly dependent through the shared Context, we run the procOrder PREFIX up to
and including that target — never an arbitrary subset. Each stage is timed and a
short human detail is synthesised from the Context after it runs, so the result
feeds straight into :func:`ytdrill.drillsidecar.emit_sidecar`.

Stdlib only (no websockets here) so it stays unit-testable.
"""
from __future__ import annotations

import time
from typing import Any, Callable

from .modules.base import Context


def _detail(ctx: Context, node: str) -> str:
    """A one-line, human description of what a stage produced, from the Context."""
    if node == "fetch_info":
        return f"{ctx.title or ctx.video_id or 'video'} [{ctx.channel}]".strip()
    if node == "transcript":
        return f"{len(ctx.segments or [])} segments, {len(ctx.transcript or '')} chars"
    if node == "summarize":
        return f"summary {len(ctx.summary or '')} chars ({ctx.summary_model})"
    if node == "emit_tiddler":
        return f"{len(ctx.tiddlers or [])} tiddlers"
    return ""


def run_to(ctx: Context, registry: dict[str, type], *, target: str,
           order: list[str],
           on_event: Callable[[str, dict], None] | None = None) -> list[dict]:
    """Execute ``order[:index(target)+1]`` and return the list of stage records
    ``[{node, cost_ms, detail}]``. ``on_event(kind, payload)`` is called with
    ``"start"``/``"done"`` around each stage so callers can stream progress.

    Raises ``ValueError`` if ``target`` is not in ``order``.
    """
    if target not in order:
        raise ValueError(f"unknown target stage {target!r}; "
                         f"known: {', '.join(order)}")
    prefix = order[:order.index(target) + 1]
    stages: list[dict] = []
    for node in prefix:
        cls = registry.get(node)
        if cls is None:
            raise ValueError(f"no module registered for stage {node!r}")
        if on_event:
            on_event("start", {"node": node})
        t0 = time.perf_counter()
        cls(ctx.config).run(ctx)
        cost_ms = (time.perf_counter() - t0) * 1000.0
        rec: dict[str, Any] = {"node": node, "cost_ms": cost_ms,
                               "detail": _detail(ctx, node)}
        stages.append(rec)
        if on_event:
            on_event("done", rec)
    return stages
