"""End-to-end test of the WebSocket pipeline-runner server glue.

Drives the real aiohttp app (HTTP serve + WS protocol + threaded pipeline +
queue-streamed stage events + PDFDRILL sidecar emit) with FAKE pipeline modules,
so it needs no network and no external tools — yet exercises every layer of
tools/ytdrillui_server.py.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, str(Path(__file__).parents[1]))


def _load_server():
    path = Path(__file__).parents[1] / "tools" / "ytdrillui_server.py"
    spec = importlib.util.spec_from_file_location("ytdrillui_server", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


SRV = _load_server()


class _FakeFetch:
    def __init__(self, cfg): pass
    def run(self, ctx):
        ctx.video_id = "testid12345"
        ctx.title = "Fake Video"
        ctx.channel = "Fake Channel"


class _FakeTranscript:
    def __init__(self, cfg): pass
    def run(self, ctx):
        ctx.transcript = "hello world"
        ctx.transcript_source = "srt"
        ctx.segments = [{"t0": 0, "t1": 1000, "text": "hello world"}]


def test_ws_run_writes_pdfdrill_sidecar(tmp_path, monkeypatch):
    monkeypatch.setattr(SRV, "REGISTRY",
                        {"fetch_info": _FakeFetch, "transcript": _FakeTranscript})
    monkeypatch.setattr(SRV, "_config",
                        lambda: {"procOrder": ["fetch_info", "transcript"],
                                 "modules": {}})

    async def body():
        app = SRV.make_app(tmp_path)
        async with TestClient(TestServer(app)) as client:
            # the page serves
            r = await client.get("/")
            assert r.status == 200
            assert "ytdrill" in (await r.text())

            ws = await client.ws_connect("/ws")
            hello = await ws.receive_json()
            assert hello["type"] == "hello"
            assert hello["stages"] == ["fetch_info", "transcript"]

            await ws.send_json({"action": "run",
                                "url": "https://youtu.be/x",
                                "target": "transcript"})

            msgs = []
            while True:
                m = await ws.receive_json()
                msgs.append(m)
                if m["type"] in ("done", "error"):
                    break

            kinds = [m["type"] for m in msgs]
            assert "started" in kinds
            assert "done" in kinds, f"no done; got {msgs}"
            # streamed a done event per stage
            done_stages = [m["node"] for m in msgs
                           if m["type"] == "stage" and m["phase"] == "done"]
            assert done_stages == ["fetch_info", "transcript"]

            done = next(m for m in msgs if m["type"] == "done")
            assert done["bibkey"] == "yttestid12345"
            assert done["sidecar"] == "yttestid12345.drill.json"
            assert "yttestid12345.drill.json" in done["artifacts"]

            await ws.close()

    asyncio.run(body())

    # the PDFDRILL-compatible artifacts are really on disk
    d = json.loads((tmp_path / "yttestid12345.drill.json").read_text())
    assert d["facts"] == ["INFO_FETCHED", "TRANSCRIPT_BUILT"]
    assert d["evidence"]["video_id"] == "testid12345"
    assert (tmp_path / "yttestid12345.drill" / "model.docmodel.json").is_file()


class _FakeSummarize:
    def __init__(self, cfg): pass
    def run(self, ctx):
        ctx.summary = "# Title\n\nbody.\n\n## References\n\n[1] a paper"
        ctx.summary_model = "fake"


def test_ws_run_surfaces_markdown_summary(tmp_path, monkeypatch):
    monkeypatch.setattr(SRV, "REGISTRY",
                        {"fetch_info": _FakeFetch, "transcript": _FakeTranscript,
                         "summarize": _FakeSummarize})
    monkeypatch.setattr(SRV, "_config",
                        lambda: {"procOrder": ["fetch_info", "transcript",
                                               "summarize"], "modules": {}})

    async def body():
        app = SRV.make_app(tmp_path)
        async with TestClient(TestServer(app)) as client:
            ws = await client.ws_connect("/ws")
            await ws.receive_json()  # hello
            await ws.send_json({"action": "run", "url": "https://youtu.be/x",
                                "target": "summarize"})
            while True:
                m = await ws.receive_json()
                if m["type"] in ("done", "error"):
                    break
            assert m["type"] == "done", m
            # the markdown summary (with its References section) is inline …
            assert "## References" in m["summary"]
            # … and written as a bibkey-named artifact the UI can link
            assert "yttestid12345.summary.md" in m["artifacts"]
            await ws.close()

    asyncio.run(body())
    assert (tmp_path / "yttestid12345.summary.md").read_text().startswith("# Title")


def test_artifact_serves_markdown_with_charset(tmp_path):
    # a MIME carrying '; charset=utf-8' (md/html/json/txt) must not crash the
    # /artifact handler — aiohttp rejects charset in the content_type argument.
    (tmp_path / "x.summary.md").write_text("# hi", encoding="utf-8")

    async def body():
        app = SRV.make_app(tmp_path)
        async with TestClient(TestServer(app)) as client:
            r = await client.get("/artifact", params={"path": "x.summary.md"})
            assert r.status == 200, await r.text()
            assert "# hi" in await r.text()
            assert r.headers["Content-Type"].startswith("text/markdown")

    asyncio.run(body())


def test_ws_unknown_target_reports_error(tmp_path, monkeypatch):
    monkeypatch.setattr(SRV, "REGISTRY", {"fetch_info": _FakeFetch})
    monkeypatch.setattr(SRV, "_config",
                        lambda: {"procOrder": ["fetch_info"], "modules": {}})

    async def body():
        app = SRV.make_app(tmp_path)
        async with TestClient(TestServer(app)) as client:
            ws = await client.ws_connect("/ws")
            await ws.receive_json()  # hello
            await ws.send_json({"action": "run", "url": "u", "target": "nope"})
            while True:
                m = await ws.receive_json()
                if m["type"] in ("done", "error"):
                    assert m["type"] == "error"
                    assert "nope" in m["message"]
                    break
            await ws.close()

    asyncio.run(body())
