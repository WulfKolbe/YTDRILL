# yt2tw

YouTube → cleaned transcript → Perplexity Sonar summary → **TiddlyWiki JSON tiddler**.

Python rewrite of the original `yt2tw.sh` + `clean_transcript.awk` pipeline,
using `yt_dlp` as a library instead of shelling out. Output of step 1 is a
TiddlyWiki JSON array containing a single tiddler per video.

## Why the rewrite

The bash version called `yt-dlp` three times per video (video, audio,
subtitles+description) — three metadata round-trips and three chances to trip
YouTube's bot detection. This version performs **one** `extract_info()` call;
every later stage reads from the cached info dict. Captions are fetched in
YouTube's native **json3** format when available: no rolling-caption
duplication to clean, and per-segment timestamps are preserved (needed later
for slide/beamer alignment). The SRT/VTT path remains as a fallback through a
Python port of `clean_transcript.awk`, verified **byte-identical** against the
gawk reference (`tests/test_yt2tw.py::test_srt_matches_awk`).

## Install

```sh
pip install yt-dlp        # only non-stdlib dependency
```

## Usage

```sh
export PERPLEXITY_API_KEY=...        # or configure modules.summarize.secret_cmd
python -m yt2tw 'https://www.youtube.com/watch?v=...'
python -m yt2tw --no-summary URL     # skip Sonar; tiddler text = transcript
python -m yt2tw --media URL          # also download video + ORIGINAL audio
python -m yt2tw --workdir /tmp/x URL # default is a fresh temp dir (never ~/Downloads)
```

The path of the emitted `<bibkey>_video_0001.json` is printed on stdout, so it
composes with `tw.py`:

```sh
python -m yt2tw --no-summary URL | xargs -I{} tw.py import {}
```

## Secrets — IMPORTANT

**Never commit an API key.** Resolution order (highest wins):

1. `PERPLEXITY_API_KEY` in the process environment
2. `.env` file — `cp .env.example .env` and fill in; search order is
   `--env PATH` > workdir > project root > cwd. `.env` and `.env.*` are
   gitignored (only `.env.example` is tracked); parser is stdlib
   (`yt2tw/env.py`), no python-dotenv dependency.
3. `modules.summarize.secret_cmd` in `config.json`, e.g. `apivault get perplexity`

The key that was hardcoded in the original `yt2tw.sh` must be considered
compromised and rotated before this repo goes public.

## Pipeline architecture

`config.json` drives a `procOrder` of additive modules (`BaseModule`
pattern); modules communicate only through the shared `Context`:

| module        | does                                                            |
|---------------|-----------------------------------------------------------------|
| `fetch_info`  | single `yt_dlp.extract_info(download=False)`; caches info dict   |
| `transcript`  | picks original-language track (manual > `*-orig` auto > auto), json3 > srt/vtt; emits plain text **and** timed segments |
| `summarize`   | Perplexity Sonar, triple no-search guard, transcript-first prompt, `prompts/howto.md` template (the original tested `howto.txt`, with the unfilled *Inputs Provided* block removed at the template level instead of via `sed`); optional `## COMMENTS` section when `modules.fetch_info.max_comments > 0` |
| `extract_references` | parses the `@type{key,...}` BibTeX entries the howto forces into the summary (brace-counting, nested braces safe) and attaches `bibtex` + `cite-keys` tiddler fields via the additive `ctx.extra_fields` contract — the direct pdfdrill handoff |
| `media`       | *(optional, `--media`)* video + **original** audio stream (`language_preference`/`format_note=original` pinning) — prep for slide isolation |
| `emit_tiddler`| single-tiddler TW JSON array, `$Bibkey_$type_$serial` naming     |

## Tiddler schema

Title `yt<videoid>_video_0001`; `text` holds the summary markdown
(`type: text/markdown`). Provenance and payload fields: `bibkey`, `url`,
`video-id`, `channel`, `uploaded`, `duration`, `language`,
`transcript-source`, `transcript-blake2b`, `summary-blake2b`,
`description`, `transcript`, `segments` (JSON: `[{t0,t1,text}]` ms),
`chapters`, `yt-tags`, and — when the summary contains BibTeX —
`bibtex` (verbatim entries, `% Inferred:` comments preserved) and
`cite-keys` (space-separated `$Bibkey`s for pdfdrill). The blake2b digests let re-runs detect caption or
description drift without diffing text.

## Roadmap hooks already in place

- **pdfdrill reference following** — the `howto.md` prompt forces a verbatim
  `## References` section; the raw `*.info.json` is persisted next to the
  tiddler for link extraction from the description.
- **Beamer lecture recovery** — `segments` keeps caption timing; `chapters`
  keeps YouTube chapter marks; the `media` module downloads the video stream
  and pins the *original* audio track on multi-audio (dubbed) videos.

## Sandbox / proxied environments

Behind a MITM proxy set `modules.fetch_info.nocheckcertificate: true` and
`YT2TW_INSECURE_SSL=1` for the caption fetch. Leave both OFF on a normal
machine. Note: YouTube's timedtext endpoint aggressively 429s datacenter
IPs regardless of client — run from a residential connection, optionally
with `modules.fetch_info.cookiesfrombrowser: "firefox"`.

## Tests

```sh
python tests/test_yt2tw.py      # includes gawk reference-equivalence check
```

## Legacy

`legacy/` contains the original bash + awk pipeline (API key redacted) for
reference.
