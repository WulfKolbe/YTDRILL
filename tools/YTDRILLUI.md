# ytdrillui — run the YTDRILL pipeline from a browser

A **minimal WebSocket tool** (deliberately *not* a REPL like PDFDRILL's drillui):
type a video URL, pick a target stage, hit **Run**. The server runs the pipeline
up to that stage, streams per-stage progress, and writes the result as
**PDFDRILL-compatible** artifacts so the two projects' outputs are interchangeable.

```
 browser tab                         one aiohttp process
┌────────────────────┐  WebSocket   ┌──────────────────────────────┐
│ ytdrillui.html     │◄────/ws─────►│ ytdrillui_server.py          │
│ url + target + Run │   HTTP /     │  • serves the page           │
│ live log + facts   │   /artifact  │  • runs the pipeline prefix  │
└────────────────────┘              │    (in a worker thread)      │
                                    │  • emits the .drill sidecar  │
                                    └───────────┬──────────────────┘
                                                │ ytdrill (package, in-process)
                                                ▼
                                   <bibkey>.drill.json + <bibkey>.drill/
```

## Run it

```bash
python3 tools/ytdrillui_server.py            # then open http://localhost:8799/
python3 tools/ytdrillui_server.py --port 8799 --outdir ./ytdrill-out
```

Needs `aiohttp` (in `requirements.txt`). The page is served by the same process,
so there is nothing else to start.

## The command model — pick a *target* stage

The pipeline modules share a `Context` and are **linearly dependent**
(`transcript` needs `fetch_info`, `summarize` needs `transcript`, …). So you do
not cherry-pick arbitrary stages — you choose a **target**, and the server runs
the `procOrder` **prefix up to and including it**:

| target you pick | stages that run | needs network / key |
|---|---|---|
| `fetch_info` | fetch_info | yt-dlp |
| `transcript` | fetch_info → transcript | yt-dlp |
| `summarize` | … → summarize | yt-dlp + Perplexity key |
| `emit_tiddler` | the whole default pipeline | yt-dlp + Perplexity key |

A **local video path** (instead of a URL) is detected automatically:
`local_source` replaces `fetch_info` and the YT-only stages are dropped, so a
`<stem>.<lang>.srt` sidecar is used as the transcript — no network.

## What it writes (the PDFDRILL contract)

Into `--outdir`, named by the tiddler `bibkey` (`yt<id>` or a local id):

- **`<bibkey>.drill.json`** — state manifest: `facts[]` (one per completed
  stage: `INFO_FETCHED`, `TRANSCRIPT_BUILT`, `SUMMARIZED`, …), `evidence{}`
  (video metadata + counts), `transitions[]` (`{ts, node, from, to, cost_ms,
  detail}`), and the nullable PDFDRILL layer fields.
- **`<bibkey>.drill/model.docmodel.json`** — the semantic graph: a `Document`
  root plus one `Segment` object per transcript segment, with timestamp
  `alignments`. This is the substrate the semantic-compiler layers (claims,
  evidence, contradictions) will attach to.

Both shapes mirror `pdfdrill`'s `<name>.drill.json` / `<name>.drill/` exactly, so
the same readers — including PDFDRILL's drillui bridge — work on YTDRILL output.

## Pieces

- `tools/ytdrillui_server.py` — aiohttp server (page + WS + `/artifact`). Glue only.
- `tools/ytdrillui.html` — the page (gruvbox; URL field, target select, live log,
  facts + artifacts rail). No build step, no external JS.
- `ytdrill/stagerun.py` — `run_to(ctx, registry, target, order)`: the
  dependency-safe prefix runner (pure, unit-tested).
- `ytdrill/drillsidecar.py` — `emit_sidecar(ctx, stages)`: the PDFDRILL-compatible
  writer (pure, unit-tested).

## Test it

```bash
python3 -m pytest tests/test_drillsidecar.py tests/test_stagerun.py \
                  tests/test_ytdrillui_server.py -q
```

The server test drives the real aiohttp app with fake modules — it exercises
HTTP serve + WS protocol + threaded pipeline + streamed events + sidecar emit
with no network and no external tools.
