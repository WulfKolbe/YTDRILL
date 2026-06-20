# ytdrill

YouTube → cleaned transcript → Perplexity Sonar summary → **TiddlyWiki JSON tiddler**.

(Formerly `yt2tw`; the project, repo and Python package were renamed to
`ytdrill`. The CLI is now `python -m ytdrill`.)

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
gawk reference (`tests/test_ytdrill.py::test_srt_matches_awk`).

## Install

```sh
pip install yt-dlp        # only non-stdlib dependency
```

## Usage

```sh
export PERPLEXITY_API_KEY=...        # or configure modules.summarize.secret_cmd
python -m ytdrill 'https://www.youtube.com/watch?v=...'
python -m ytdrill --no-summary URL     # skip Sonar; tiddler text = transcript
python -m ytdrill --media URL          # also download video + ORIGINAL audio
python -m ytdrill --slides URL         # slide frames -> searchable OCR'd PDF
                                     # (implies --media; needs ffmpeg/tesseract/gs)
python -m ytdrill --workdir /tmp/x URL # default is a fresh temp dir (never ~/Downloads)
python -m ytdrill /path/to/video.mkv   # LOCAL file (e.g. 4K Video Downloader+ export):
                                     # transcript from sidecar <stem>.<lang>.srt,
                                     # --slides works directly on the file
```

The path of the emitted `<bibkey>_video_0001.json` is printed on stdout, so it
composes with `tw.py`:

```sh
python -m ytdrill --no-summary URL | xargs -I{} tw.py import {}
```

## Secrets — IMPORTANT

**Never commit an API key.** Resolution order (highest wins):

1. `PERPLEXITY_API_KEY` in the process environment
2. `.env` file — `cp .env.example .env` and fill in; search order is
   `--env PATH` > workdir > project root > cwd. `.env` and `.env.*` are
   gitignored (only `.env.example` is tracked); parser is stdlib
   (`ytdrill/env.py`), no python-dotenv dependency.
3. `modules.summarize.secret_cmd` in `config.json`, e.g. `apivault get perplexity`

The key that was hardcoded in the original `yt2tw.sh` must be considered
compromised and rotated before this repo goes public.

## Pipeline architecture

`ytdrill/planner.py` (`run_plan`) drives a **lazy, layer-wise** state machine
over additive modules (`BaseModule` pattern) that communicate only through the
shared `Context`. It escalates only as far as the requested artifacts need:

    info → transcript (captions) → audio + ASR (iff no captions) → video (slides only)

So a transcript/summary run **never downloads the video**; the video stream is
fetched only for `--slides`, and the audio stream only when a video has no
caption track. The modules:

| module        | does                                                            |
|---------------|-----------------------------------------------------------------|
| `fetch_info`  | single `yt_dlp.extract_info(download=False)`; caches info dict   |
| `local_source`| *(local files)* replaces fetch_info+transcript+media: metadata via ffprobe, transcript from sidecar `<stem>.<lang>.srt` (`lang_priority` config), bibkey `loc<blake2b(stem)[:11]>`, video ready for `slides` |
| `transcript`  | picks original-language track (manual > `*-orig` auto > auto), json3 > srt/vtt; emits plain text **and** timed segments |
| `audio`       | *(lazy fallback)* downloads the **original audio stream only** (never the video) — runs only when `transcript` found no captions |
| `asr`         | *(lazy fallback)* Whisper (`faster-whisper`, CPU `int8`) over the downloaded audio → fills the same `transcript`/`segments` as the caption path; `modules.asr.model` (default `base`) |
| `summarize`   | Perplexity Sonar, triple no-search guard, transcript-first prompt, `prompts/howto.md` template (the original tested `howto.txt`, with the unfilled *Inputs Provided* block removed at the template level instead of via `sed`); optional `## COMMENTS` section when `modules.fetch_info.max_comments > 0` |
| `extract_references` | parses the `@type{key,...}` BibTeX entries the howto forces into the summary (brace-counting, nested braces safe) and attaches `bibtex` + `cite-keys` tiddler fields via the additive `ctx.extra_fields` contract — the direct pdfdrill handoff |
| `media`       | *(optional, `--media`)* video + **original** audio stream (`language_preference`/`format_note=original` pinning) — prep for slide isolation |
| `slides`      | *(optional, `--slides`)* license-clean vid2slides replacement: ffmpeg scene-change frames + chapter marks → dHash dedupe (stdlib PGM parse, no new Python deps) → per-frame Tesseract OCR → Ghostscript merge into `<bibkey>_slides.pdf`; attaches `slides-pdf` + `slide-times` fields — the PDF is the pdfdrill handoff |
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
`YTDRILL_INSECURE_SSL=1` for the caption fetch. Leave both OFF on a normal
machine. Note: YouTube's timedtext endpoint aggressively 429s datacenter
IPs regardless of client — run from a residential connection, optionally
with `modules.fetch_info.cookiesfrombrowser: "firefox"`.

## Tests

```sh
python tests/test_ytdrill.py      # includes gawk reference-equivalence check
```

## Legacy

`legacy/` contains the original bash + awk pipeline (API key redacted) for
reference.
