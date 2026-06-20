#!/usr/bin/env python3
"""ytdrillui_server — a tiny WebSocket page that runs the YTDRILL pipeline.

A minimal browser tool (NOT a REPL): you type a video URL and pick a target
stage; the server runs the pipeline PREFIX up to that stage, streams per-stage
progress over a WebSocket, and writes the result as PDFDRILL-compatible
artifacts (`<bibkey>.drill.json` + `<bibkey>.drill/`) so YTDRILL and PDFDRILL
outputs are interchangeable.

  python3 tools/ytdrillui_server.py [--port 8799] [--outdir ./ytdrill-out]

One aiohttp process serves the page, the WebSocket, and the artifact files on a
single port. The blocking pipeline runs in a thread; stage events are pushed
back to the socket through an asyncio queue.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from aiohttp import web, WSMsgType

# import ytdrill as a package whether or not it's installed
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ytdrill.cli import REGISTRY, DEFAULT_CONFIG                    # noqa: E402
from ytdrill.modules.base import Context, load_config, bibkey_of   # noqa: E402
from ytdrill.env import load_env                                   # noqa: E402
from ytdrill.stagerun import run_to                                # noqa: E402
from ytdrill.drillsidecar import emit_sidecar                      # noqa: E402

HTML_PATH = Path(__file__).resolve().parent / "ytdrillui.html"
REPO_ROOT = Path(__file__).resolve().parents[1]
OUTDIR = web.AppKey("outdir", Path)

MIME = {".html": "text/html; charset=utf-8", ".json": "application/json",
        ".md": "text/markdown; charset=utf-8", ".txt": "text/plain",
        ".pdf": "application/pdf", ".svg": "image/svg+xml", ".png": "image/png"}


def _config() -> dict:
    cfg_file = REPO_ROOT / "config.json"
    return load_config(cfg_file) if cfg_file.is_file() else dict(DEFAULT_CONFIG)


def _order(config: dict) -> list[str]:
    return list(config.get("procOrder", DEFAULT_CONFIG["procOrder"]))


def _adjust_for_local(url: str, order: list[str]) -> list[str]:
    """Local file: local_source replaces fetch_info; drop the YT-only stages
    (mirrors ytdrill/cli.py so the WS tool handles local videos too)."""
    if not Path(url).expanduser().is_file():
        return order
    return ["local_source" if m == "fetch_info" else m
            for m in order if m not in ("transcript", "media")]


def _run_pipeline(url: str, target: str, outdir: Path, emit) -> dict:
    """Blocking: build a Context, run the prefix up to `target`, emit sidecar.
    Returns a JSON-able result dict. Runs in a worker thread."""
    config = _config()
    order = _adjust_for_local(url, _order(config))
    config["procOrder"] = order
    outdir.mkdir(parents=True, exist_ok=True)
    load_env(search=[outdir, REPO_ROOT, Path.cwd()])

    ctx = Context(url=url, workdir=outdir, config=config)
    stages = run_to(ctx, REGISTRY, target=target, order=order, on_event=emit)
    sidecar = emit_sidecar(ctx, stages=stages)

    bibkey = bibkey_of(ctx)
    # Surface the readable summary — the Perplexity markdown already carries its
    # own References / BibTeX sections. Write it bibkey-named so it shows in the
    # artifacts rail, AND return it inline so the UI can display it directly.
    summary = ctx.summary or ""
    if summary:
        (outdir / f"{bibkey}.summary.md").write_text(summary, encoding="utf-8")
    artifacts = sorted(
        str(p.relative_to(outdir)) for p in outdir.rglob(f"{bibkey}*")
        if p.is_file())
    return {"bibkey": bibkey, "sidecar": sidecar.name, "summary": summary,
            "facts": [s["node"] for s in stages], "artifacts": artifacts}


async def ws_handler(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    outdir: Path = request.app[OUTDIR]
    order = _order(_config())
    await ws.send_json({"type": "hello", "stages": order, "outdir": str(outdir)})

    async for msg in ws:
        if msg.type != WSMsgType.TEXT:
            continue
        try:
            data = msg.json()
        except Exception:
            continue
        if data.get("action") != "run":
            continue
        url = str(data.get("url", "")).strip()
        target = str(data.get("target", "")).strip()
        if not url or not target:
            await ws.send_json({"type": "error", "message": "url and target required"})
            continue

        loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue()

        def emit(kind: str, payload: dict):
            loop.call_soon_threadsafe(q.put_nowait, (kind, payload))

        await ws.send_json({"type": "started", "url": url, "target": target})
        fut = loop.run_in_executor(None, _run_pipeline, url, target, outdir, emit)
        while not fut.done() or not q.empty():
            try:
                kind, payload = await asyncio.wait_for(q.get(), timeout=0.2)
            except asyncio.TimeoutError:
                continue
            await ws.send_json({"type": "stage", "phase": kind, **payload})
        try:
            result = await fut
            await ws.send_json({"type": "done", **result})
        except Exception as e:  # surface the failure, keep the socket alive
            await ws.send_json({"type": "error", "message": f"{type(e).__name__}: {e}"})
    return ws


async def index(request: web.Request) -> web.Response:
    if not HTML_PATH.is_file():
        return web.Response(status=404, text="ytdrillui.html not found")
    return web.Response(body=HTML_PATH.read_bytes(),
                        content_type="text/html", charset="utf-8",
                        headers={"cache-control": "no-store"})


async def artifact(request: web.Request) -> web.Response:
    rel = request.query.get("path", "")
    outdir: Path = request.app[OUTDIR]
    target = (outdir / rel).resolve()
    if outdir.resolve() not in target.parents and target != outdir.resolve():
        return web.Response(status=403, text="forbidden path")
    if not target.is_file():
        return web.Response(status=404, text="not found")
    return web.Response(body=target.read_bytes(),
                        content_type=MIME.get(target.suffix.lower(),
                                              "application/octet-stream"))


def make_app(outdir: Path) -> web.Application:
    app = web.Application()
    app[OUTDIR] = outdir
    app.router.add_get("/", index)
    app.router.add_get("/ws", ws_handler)
    app.router.add_get("/artifact", artifact)
    return app


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ytdrillui_server.py")
    ap.add_argument("--port", type=int, default=8799)
    ap.add_argument("--outdir", default="./ytdrill-out",
                    help="where <bibkey>.drill.json / .drill/ are written")
    args = ap.parse_args(argv)
    outdir = Path(args.outdir).expanduser().resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    print(f"ytdrillui → http://localhost:{args.port}/   (outdir: {outdir})")
    web.run_app(make_app(outdir), host="localhost", port=args.port,
                print=None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
