"""Tests for ytdrill. Run: python -m pytest tests/ -q  (or python tests/test_ytdrill.py)

test_srt_matches_awk additionally executes the ORIGINAL clean_transcript.awk
(if gawk + the script are available) and asserts byte-identical plain-text
output — the port is verified against the reference implementation.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from ytdrill.clean import clean_srt, clean_json3            # noqa: E402
from ytdrill.modules.emit import EmitTiddler, tw_now, b2    # noqa: E402
from ytdrill.modules.base import Context                    # noqa: E402

SAMPLE_SRT = """\
1
00:00:00,000 --> 00:00:02,500
hello world this is

2
00:00:02,500 --> 00:00:05,000
hello world this is
a rolling caption

3
00:00:05,000 --> 00:00:07,000
a rolling caption
with duplicated lines

garbage block without timestamp
should be skipped entirely

4
00:00:07,000 --> 00:00:09,000
\r
final line\r
"""

SAMPLE_JSON3 = json.dumps({
    "events": [
        {"tStartMs": 0, "dDurationMs": 2500,
         "segs": [{"utf8": "hello "}, {"utf8": "world"}]},
        {"tStartMs": 2500, "dDurationMs": 100, "segs": [{"utf8": "\n"}]},
        {"tStartMs": 2600, "dDurationMs": 2400,
         "segs": [{"utf8": "second  event"}]},
    ]
})

AWK = Path(__file__).resolve().parent.parent / "legacy" / "clean_transcript.awk"


def test_srt_clean():
    text, segs = clean_srt(SAMPLE_SRT)
    assert text == ("hello world this is a rolling caption "
                    "with duplicated lines final line")
    assert len(segs) == 4
    assert segs[0] == {"t0": 0, "t1": 2500, "text": "hello world this is"}
    assert segs[1]["text"] == "a rolling caption"      # dup line suppressed


def test_srt_matches_awk():
    gawk = shutil.which("gawk") or shutil.which("awk")
    if not (gawk and AWK.is_file()):
        print("  (gawk or awk script unavailable — reference check skipped)")
        return
    ref = subprocess.run([gawk, "-f", str(AWK)], input=SAMPLE_SRT,
                         capture_output=True, text=True).stdout.rstrip("\n")
    ours, _ = clean_srt(SAMPLE_SRT)
    assert ours == ref, f"port diverges from awk:\n awk: {ref!r}\n py : {ours!r}"


def test_json3_clean():
    text, segs = clean_json3(SAMPLE_JSON3)
    assert text == "hello world second event"
    assert segs == [
        {"t0": 0, "t1": 2500, "text": "hello world"},
        {"t0": 2600, "t1": 5000, "text": "second event"},
    ]


def test_pick_track_prefers_base_orig_over_translation():
    # 2026-09: YouTube reports language "en-US" while the original ASR
    # track is keyed "en-orig"; plain "en" is a machine translation
    # (tlang=en) that 429s. The -orig track must win.
    from ytdrill.modules.yt import Transcript
    autos = {"en": [{"ext": "json3", "url": "tlang"}],
             "en-orig": [{"ext": "json3", "url": "orig"}]}
    track, name = Transcript._pick_track({}, autos, "en-US")
    assert name == "auto:en-orig"
    assert track[0]["url"] == "orig"


def test_perplexity_chat_uses_responses_api(monkeypatch=None):
    # 2026-10: Sonar moved to the Agent API — /chat/completions answers
    # 403 agent_api_migration_required. Request shape: instructions+input
    # on /v1/responses, model "perplexity/sonar", no preset (= no search).
    import io
    import urllib.request
    from ytdrill import perplexity
    seen = {}
    body = {"output": [{"type": "message", "role": "assistant", "content": [
        {"type": "output_text", "text": "Hello "},
        {"type": "output_text", "text": "world"}]}]}

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        seen["payload"] = json.loads(req.data)
        return io.BytesIO(json.dumps(body).encode())

    orig = urllib.request.urlopen
    urllib.request.urlopen = fake_urlopen
    try:
        out = perplexity.chat("k", "sonar", "SYS", "USER",
                              max_tokens=100, temperature=0.3)
    finally:
        urllib.request.urlopen = orig
    assert out == "Hello world"
    assert seen["url"] == "https://api.perplexity.ai/v1/responses"
    pl = seen["payload"]
    assert pl["model"] == "perplexity/sonar"     # legacy name mapped
    assert pl["instructions"] == "SYS" and pl["input"] == "USER"
    assert pl["max_output_tokens"] == 100 and pl["temperature"] == 0.3
    assert "preset" not in pl and "tools" not in pl


def test_audio_download_extracts_in_its_own_session(tmp_path=None):
    # 2026-10: YouTube binds stream URLs to the session that extracted
    # them; replaying the cached info dict in a new YoutubeDL session
    # (process_ie_result) 403s. Downloads must re-extract in-session.
    import tempfile
    from ytdrill.modules import yt
    calls = []

    class FakeYDL:
        def __init__(self, opts): self.opts = opts
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def extract_info(self, url, download=False):
            calls.append(("extract_info", url, download, self.opts["format"]))
            return {"requested_downloads": [{"filepath": "/x/a.webm"}]}
        def process_ie_result(self, *a, **k):
            calls.append(("process_ie_result",))
            return {}

    orig = yt._ydl
    yt._ydl = lambda workdir, **extra: FakeYDL(extra)
    try:
        ctx = Context(url="https://youtu.be/x",
                      workdir=Path(tmp_path or tempfile.mkdtemp()),
                      config={})
        ctx.info = {"formats": []}
        yt.AudioDownload({}).run(ctx)
    finally:
        yt._ydl = orig
    assert calls == [("extract_info", "https://youtu.be/x", True, "bestaudio")]
    assert str(ctx.audio_path) == "/x/a.webm"


def _fake_ydl_factory(results_by_client, calls):
    """_ydl stand-in: extract_info returns results_by_client[player_client]."""
    class FakeYDL:
        def __init__(self, opts): self.opts = opts
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def extract_info(self, url, download=False):
            client = (self.opts.get("extractor_args", {})
                      .get("youtube", {}).get("player_client", ["default"])[0])
            calls.append(client)
            return results_by_client.get(client, {})
    return lambda workdir, **extra: FakeYDL(extra)


def test_caption_candidates_cover_every_language():
    # Any language will do (Perplexity reads them all); original ASR
    # first, translations (plain auto keys beside an -orig) last.
    from ytdrill.modules.yt import Transcript
    subs = {"de": [{"ext": "json3", "url": "de"}]}
    autos = {"fr-orig": [{"ext": "json3", "url": "fr-orig"}],
             "fr": [{"ext": "json3", "url": "fr"}],
             "en": [{"ext": "json3", "url": "en-tl"}]}
    names = [n for _, n in Transcript._caption_candidates(subs, autos, "")]
    assert names == ["auto:fr-orig", "sub:de", "auto:fr", "auto:en"]
    names = [n for _, n in Transcript._caption_candidates(subs, autos, "de-DE")]
    assert names[0] == "sub:de"


def test_transcript_falls_through_failed_tracks(tmp_path=None):
    import tempfile
    import urllib.error
    from ytdrill.modules import yt
    tried = []

    def fake_download(url, retries=5):
        tried.append(url)
        if url == "bad":
            raise urllib.error.HTTPError(url, 429, "Too Many", {}, None)
        return SAMPLE_JSON3

    ctx = Context(url="u", workdir=Path(tmp_path or tempfile.mkdtemp()),
                  config={})
    ctx.info = {"automatic_captions": {
        "en-orig": [{"ext": "json3", "url": "bad"}],
        "de-orig": [{"ext": "json3", "url": "good"}]}}
    orig = yt.Transcript._download
    yt.Transcript._download = staticmethod(fake_download)
    try:
        yt.Transcript({}).run(ctx)
    finally:
        yt.Transcript._download = orig
    assert tried == ["bad", "good"]
    assert ctx.transcript == "hello world second event"


def test_transcript_probes_other_clients_before_giving_up(tmp_path=None):
    import tempfile
    from ytdrill.modules import yt
    calls = []
    found = {"automatic_captions": {"ja-orig": [{"ext": "json3", "url": "ja"}]}}
    ctx = Context(url="u", workdir=Path(tmp_path or tempfile.mkdtemp()),
                  config={})
    ctx.info = {}                                  # default client: nothing
    orig_ydl, orig_dl = yt._ydl, yt.Transcript._download
    yt._ydl = _fake_ydl_factory({"mweb": found}, calls)
    yt.Transcript._download = staticmethod(lambda url, retries=5: SAMPLE_JSON3)
    try:
        yt.Transcript({"fallback_clients": ["web_safari", "mweb", "ios"]}).run(ctx)
    finally:
        yt._ydl, yt.Transcript._download = orig_ydl, orig_dl
    assert calls == ["web_safari", "mweb"]          # stops at the first hit
    assert ctx.transcript == "hello world second event"


def test_list_subs_reports_every_client_and_language():
    from ytdrill.modules import yt
    calls = []
    results = {
        "default": {"subtitles": {"de": [{"ext": "vtt"}, {"ext": "json3"}]},
                    "automatic_captions": {"en-orig": [{"ext": "json3"}],
                                           "en": [{"ext": "json3"}],
                                           "fr": [{"ext": "json3"}]}},
        "ios": {},
    }
    orig = yt._ydl
    yt._ydl = _fake_ydl_factory(results, calls)
    try:
        text = yt.list_subs("u", Path("."), clients=["default", "ios"])
    finally:
        yt._ydl = orig
    assert calls == ["default", "ios"]
    assert "default" in text and "ios" in text
    assert "sub   de" in text and "vtt, json3" in text
    assert "auto  en-orig" in text
    assert "translated: en, fr" in text
    assert "(none)" in text                          # ios has nothing


def test_emit_tiddler(tmp_path=None):
    import tempfile
    tmp = Path(tmp_path or tempfile.mkdtemp())
    cfg = {"modules": {"emit_tiddler": {"tiddler_type": "video"}}}
    ctx = Context(url="https://youtu.be/dQw4w9WgXcQ", workdir=tmp, config=cfg)
    ctx.video_id = "dQw4w9WgXcQ"
    ctx.title = "Test Video"
    ctx.channel = "Test Channel"
    ctx.transcript = "hello world"
    ctx.summary = "# Summary\nhello."
    ctx.segments = [{"t0": 0, "t1": 1000, "text": "hello world"}]
    EmitTiddler(cfg).run(ctx)

    data = json.loads(ctx.output_path.read_text(encoding="utf-8"))
    assert isinstance(data, list) and len(data) == 2     # summary + html
    t = data[0]
    assert t["title"] == "ytdQw4w9WgXcQ_video_0001"
    assert t["bibkey"] == "ytdQw4w9WgXcQ"
    # summary transcludes the html video tiddler, then links the source
    assert t["text"].startswith("{{ytdQw4w9WgXcQ_html_0001}}")
    assert "(https://youtu.be/dQw4w9WgXcQ)" in t["text"]
    assert "# Summary" in t["text"]
    assert t["transcript-blake2b"] == b2("hello world")
    assert json.loads(t["segments"])[0]["t1"] == 1000
    assert "[[Test Channel]]" in t["tags"]
    assert "[[ytdrill]]" in t["tags"] and "[[yt2tw]]" not in t["tags"]
    assert len(t["created"]) == 17 and t["created"].isdigit()

    h = data[1]
    assert h["title"] == "ytdQw4w9WgXcQ_html_0001"
    assert h["type"] == "text/html"
    assert "youtube.com/embed/dQw4w9WgXcQ" in h["text"]
    assert h["bibkey"] == "ytdQw4w9WgXcQ"


def test_build_video_html():
    from ytdrill.modules.emit import build_video_html
    yt = build_video_html("My Title", youtube_id="dQw4w9WgXcQ")
    assert "youtube.com/embed/dQw4w9WgXcQ" in yt
    assert "video-container" in yt and "My Title" in yt
    loc = build_video_html("Local Vid", video_url="file:///x/My Vid.mkv")
    assert "<video" in loc and "file:///x/My Vid.mkv" in loc
    assert "video/mp4" in loc


def test_emit_two_tiddlers_local():
    import tempfile
    tmp = Path(tempfile.mkdtemp())
    cfg = {"modules": {"emit_tiddler": {}}}
    ctx = Context(url="/data/My Lecture.mkv", workdir=tmp, config=cfg)
    ctx.title = "My Lecture"
    ctx.summary = "# S"
    ctx.bibkey = "locdeadbeef42"
    ctx.video_path = Path("/data/My Lecture.mkv")
    ctx.extra_fields = {"slides-pdf": "locdeadbeef42_slides.pdf"}
    EmitTiddler(cfg).run(ctx)
    data = json.loads(ctx.output_path.read_text())
    assert len(data) == 2
    assert data[0]["text"].startswith("{{locdeadbeef42_html_0001}}")
    # summary links BOTH the video and the slides PDF before the markdown
    assert "file:///data/My%20Lecture.mkv" in data[0]["text"]
    assert "locdeadbeef42_slides.pdf)" in data[0]["text"]
    assert "\n\n# S" in data[0]["text"]
    assert "<video" in data[1]["text"]
    assert "file:///data/My%20Lecture.mkv" in data[1]["text"]





# ---------------------------------------------------------------------------
def test_env_loader():
    import os
    import tempfile
    from ytdrill.env import parse_env, load_env
    text = """
# comment
export PERPLEXITY_API_KEY="pplx-abc123"
PLAIN=value # trailing comment
QUOTED='hello world'
BROKEN LINE IGNORED
"""
    d = parse_env(text)
    assert d == {"PERPLEXITY_API_KEY": "pplx-abc123",
                 "PLAIN": "value", "QUOTED": "hello world"}
    tmp = Path(tempfile.mkdtemp())
    (tmp / ".env").write_text("Y2T_TESTKEY=fromfile\n")
    os.environ["Y2T_TESTKEY2"] = "fromproc"
    (tmp / ".env").write_text("Y2T_TESTKEY=fromfile\nY2T_TESTKEY2=shadowed\n")
    load_env(search=[tmp])
    assert os.environ["Y2T_TESTKEY"] == "fromfile"
    assert os.environ["Y2T_TESTKEY2"] == "fromproc"   # process env wins


def test_extract_references():
    from ytdrill.modules.references import extract_bibtex, ExtractReferences
    summary = r"""
Text with \cite{sciama1953}.

## BibTeX Entries

@article{sciama1953,
  author = {Sciama, D. W.},
  title = {On the {Origin} of Inertia},
  year = {1953},
  % Inferred: abstract content
}

@book{mach1883,
  author = {Mach, Ernst},
  title = {Die Mechanik in ihrer Entwicklung},
  year = {1883},
}
"""
    entries = extract_bibtex(summary)
    assert [(t, k) for t, k, _ in entries] == [
        ("article", "sciama1953"), ("book", "mach1883")]
    assert "{Origin}" in entries[0][2]           # nested braces survive
    assert entries[0][2].endswith("}")

    import tempfile
    cfg = {"modules": {}}
    ctx = Context(url="u", workdir=Path(tempfile.mkdtemp()), config=cfg)
    ctx.summary = summary
    ExtractReferences(cfg).run(ctx)
    assert ctx.extra_fields["cite-keys"] == "sciama1953 mach1883"


def test_extra_fields_reach_tiddler():
    import tempfile
    cfg = {"modules": {"emit_tiddler": {}}}
    ctx = Context(url="u", workdir=Path(tempfile.mkdtemp()), config=cfg)
    ctx.video_id = "abc"
    ctx.title = "T"
    ctx.summary = "s"
    ctx.extra_fields = {"bibtex": "@misc{x,}", "cite-keys": "x"}
    EmitTiddler(cfg).run(ctx)
    t = json.loads(ctx.output_path.read_text())[0]
    assert t["bibtex"] == "@misc{x,}" and t["cite-keys"] == "x"


def test_pgm_parse():
    from ytdrill.modules.slides import parse_pgm
    data = b"P5\n9 8\n255\n" + bytes(range(72))
    w, h, px = parse_pgm(data)
    assert (w, h) == (9, 8)
    assert px == list(range(72))


def test_dhash_hamming():
    from ytdrill.modules.slides import dhash, hamming
    inc_row = list(range(0, 90, 10))                 # strictly increasing, 9 px
    inc = inc_row * 8                                # 9x8 gradient
    dec = inc_row[::-1] * 8
    h_inc, h_dec = dhash(inc, 9, 8), dhash(dec, 9, 8)
    assert h_inc == (1 << 64) - 1                    # every neighbour brighter
    assert h_dec == 0
    assert hamming(h_inc, h_inc) == 0
    assert hamming(h_inc, h_dec) == 64


def test_slide_dedupe():
    from ytdrill.modules.slides import dedupe
    near_dup = 0b111                                 # 3 bits from frame one
    far = (1 << 64) - 1
    frames = [("f1", 0), ("f2", near_dup), ("f3", far)]
    assert dedupe(frames, max_distance=4) == ["f1", "f3"]
    # a slide revisited later still counts as duplicate of an EARLIER keep
    frames = [("f1", 0), ("f2", far), ("f3", 0b1)]
    assert dedupe(frames, max_distance=4) == ["f1", "f2"]


def test_hash_thumb_tolerates_bad_input():
    from ytdrill.modules.slides import hash_thumb
    assert hash_thumb(b"") is None                       # killed-run leftover
    assert hash_thumb(b"\x89PNG not a pgm") is None
    valid = b"P5\n9 8\n255\n" + bytes(range(72))
    assert isinstance(hash_thumb(valid), int)


def test_clean_frame_dir_removes_stale_files():
    import tempfile
    from ytdrill.modules.slides import clean_frame_dir
    d = Path(tempfile.mkdtemp()) / "slide_frames"
    clean_frame_dir(d)                                   # creates
    (d / "scene_00015.png").touch()                      # stale 0-byte frame
    (d / "scene_00015.pdf").touch()                      # stale OCR page
    clean_frame_dir(d)
    assert list(d.iterdir()) == []


def test_subprocesses_do_not_eat_stdin():
    """Regression: ffmpeg inherited the batch loop's stdin and swallowed
    bytes from the `find -print0` pipe, mangling every following path."""
    import os
    from ytdrill.modules.slides import _run
    payload = b"testdata/precious next path\0"
    r, w = os.pipe()
    os.write(w, payload)
    os.close(w)
    saved = os.dup(0)
    try:
        os.dup2(r, 0)
        _run(["cat"])                  # must NOT read the parent's stdin
        leftover = os.read(0, 1024)
    finally:
        os.dup2(saved, 0)
        os.close(saved)
        os.close(r)
    assert leftover == payload, "subprocess consumed parent stdin"


def test_local_sidecar_discovery():
    import tempfile
    from ytdrill.modules.local import find_sidecar_subs, pick_sub
    d = Path(tempfile.mkdtemp())
    video = d / "My Lecture (Part 1).mkv"
    video.touch()
    (d / "My Lecture (Part 1).en.srt").touch()
    (d / "My Lecture (Part 1).de.srt").touch()
    (d / "My Lecture (Part 1).srt").touch()
    (d / "Other Video.en.srt").touch()           # different stem: excluded
    subs = find_sidecar_subs(video)
    assert sorted(subs) == ["", "de", "en"]
    assert pick_sub(subs, ["en", "de"])[0] == "en"
    assert pick_sub(subs, ["fr", "de"])[0] == "de"
    assert pick_sub(subs, ["fr"])[0] in ("", "de", "en")   # fallback: any
    assert pick_sub({}, ["en"]) is None


def test_local_id_deterministic():
    from ytdrill.modules.local import local_id
    a, b = local_id("My Lecture"), local_id("My Lecture")
    assert a == b and len(a) == 11
    assert local_id("Other") != a


def test_bibkey_of_shared_helper():
    import tempfile
    from ytdrill.modules.base import bibkey_of
    ctx = Context(url="u", workdir=Path(tempfile.mkdtemp()), config={})
    ctx.video_id = "abc123"
    assert bibkey_of(ctx) == "ytabc123"
    ctx.bibkey = "locdeadbeef42"
    assert bibkey_of(ctx) == "locdeadbeef42"   # slides PDF must match tiddler


def test_emit_bibkey_override():
    import tempfile
    cfg = {"modules": {"emit_tiddler": {}}}
    ctx = Context(url="u", workdir=Path(tempfile.mkdtemp()), config=cfg)
    ctx.title = "T"
    ctx.summary = "s"
    ctx.bibkey = "locAbCdEf12345"
    EmitTiddler(cfg).run(ctx)
    t = json.loads(ctx.output_path.read_text())[0]
    assert t["title"] == "locAbCdEf12345_video_0001"
    assert t["bibkey"] == "locAbCdEf12345"


def test_audio_layer_format_excludes_video():
    from ytdrill.modules.yt import AudioDownload
    fmt = AudioDownload.format_for({}, {})
    assert "bestaudio" in fmt and "bestvideo" not in fmt


def _plan_reg(ran, *, captions):
    """Fake registry whose modules append their name to `ran`. transcript /
    local_source / asr set a transcript via the `captions` toggle."""
    def mk(name, effect=None):
        class M:
            def __init__(self, cfg): self.cfg = cfg
            def run(self, ctx):
                ran.append(name)
                if effect:
                    effect(ctx)
        return M
    fill = lambda ctx: setattr(ctx, "transcript", "words")
    return {
        "fetch_info": mk("fetch_info"),
        "transcript": mk("transcript", fill if captions else None),
        "local_source": mk("local_source", fill),
        "audio": mk("audio"),
        "asr": mk("asr", fill),               # ASR fills the transcript
        "summarize": mk("summarize"),
        "extract_references": mk("extract_references"),
        "slide_outline": mk("slide_outline"),
        "video": mk("video"), "slides": mk("slides"),
        "emit_tiddler": mk("emit_tiddler"),
    }


def test_run_plan_lazy_and_composable():
    from ytdrill.planner import run_plan
    import tempfile
    ran: list[str] = []
    fresh = lambda: Context(url="u", workdir=Path(tempfile.mkdtemp()), config={})

    # URL + captions + summary → no media of any kind
    ran.clear()
    run_plan(fresh(), _plan_reg(ran, captions=True),
             is_local=False, want_summary=True, want_slides=False)
    assert ran == ["fetch_info", "transcript", "summarize",
                   "extract_references", "emit_tiddler"]

    # URL + NO captions → audio THEN asr (never video)
    ran.clear()
    run_plan(fresh(), _plan_reg(ran, captions=False),
             is_local=False, want_summary=True, want_slides=False)
    assert ran[:4] == ["fetch_info", "transcript", "audio", "asr"]
    assert "video" not in ran

    # URL + NO captions but want_asr=False → no audio/asr at all
    # (the server's "summary from the first layers only, no Whisper" mode)
    ran.clear()
    run_plan(fresh(), _plan_reg(ran, captions=False),
             is_local=False, want_summary=True, want_slides=False, want_asr=False)
    assert "audio" not in ran and "asr" not in ran
    assert ran == ["fetch_info", "transcript", "summarize",
                   "extract_references", "emit_tiddler"]

    # URL --slides keeps the summary AND fetches video before the OCR stage
    ran.clear()
    run_plan(fresh(), _plan_reg(ran, captions=True),
             is_local=False, want_summary=True, want_slides=True)
    assert "summarize" in ran and ran.index("video") < ran.index("slides")

    # URL --slides --no-summary → no summary; video still precedes slides
    ran.clear()
    run_plan(fresh(), _plan_reg(ran, captions=True),
             is_local=False, want_summary=False, want_slides=True)
    assert "summarize" not in ran and ran.index("video") < ran.index("slides")

    # LOCAL file: local_source is the source; NEVER fetch_info/transcript/audio/video
    ran.clear()
    run_plan(fresh(), _plan_reg(ran, captions=True),
             is_local=True, want_summary=True, want_slides=True)
    assert ran[0] == "local_source"
    assert not ({"fetch_info", "transcript", "audio", "video"} & set(ran))
    assert "slides" in ran and ran[-1] == "emit_tiddler"


def test_asr_segments_from_whisper():
    from ytdrill.modules.asr import segments_from_whisper
    segs = segments_from_whisper([
        {"start": 0.0, "end": 2.5, "text": " hello"},
        {"start": 2.5, "end": 4.0, "text": "world "},
    ])
    assert segs == [
        {"t0": 0, "t1": 2500, "text": "hello"},
        {"t0": 2500, "t1": 4000, "text": "world"},
    ]


def test_asr_skips_without_audio():
    import tempfile
    from ytdrill.modules.asr import WhisperASR
    ctx = Context(url="u", workdir=Path(tempfile.mkdtemp()), config={})
    WhisperASR({}).run(ctx)               # no audio_path → graceful no-op
    assert ctx.transcript == "" and ctx.segments == []


def test_to_textscan_text_normalizes():
    from ytdrill.series import to_textscan_text
    md = ("## Abstract\n\nHello world [1].\n\n- first\n* second\n\n"
          "## References\n\n[1] A paper\n\n"
          "## BibTeX Entries\n\n@article{x2020,\n  title={T},\n}\n")
    out = to_textscan_text(md)
    assert "Abstract" in out and "## Abstract" not in out   # heading -> bare line
    assert "• first" in out and "• second" in out           # -/* bullets -> dot
    assert "Hello world [1]." in out                        # inline citation kept
    assert "References" in out and "[1] A paper" in out      # biblio kept
    assert "@article" not in out and "BibTeX" not in out     # bibtex section dropped
    assert "#" not in out                                    # no markdown left


def test_build_article_txt_orders_and_titles():
    from ytdrill.series import build_article_txt
    videos = [
        {"title": "Video One", "summary": "## Intro\n\nfoo [1]."},
        {"title": "Video Two", "summary": "## Intro\n\nbar."},
    ]
    out = build_article_txt("My Series", videos)
    assert out.index("My Series") < out.index("Video One") < out.index("Video Two")
    assert "foo [1]." in out and "#" not in out


def test_build_deck_txt_uses_deck_field():
    from ytdrill.series import build_deck_txt
    out = build_deck_txt("S", [{"title": "V1", "deck": "## Frame A\n\n- point"}])
    assert "Frame A" in out and "• point" in out and "#" not in out


def test_run_plan_slide_outline_after_summary():
    from ytdrill.planner import run_plan
    import tempfile
    ran: list[str] = []
    run_plan(Context(url="u", workdir=Path(tempfile.mkdtemp()), config={}),
             _plan_reg(ran, captions=True), is_local=False,
             want_summary=True, want_slide_outline=True)
    assert "slide_outline" in ran
    assert ran.index("summarize") < ran.index("slide_outline") < ran.index("emit_tiddler")
    # not requested → absent
    ran.clear()
    run_plan(Context(url="u", workdir=Path(tempfile.mkdtemp()), config={}),
             _plan_reg(ran, captions=True), is_local=False,
             want_summary=True, want_slide_outline=False)
    assert "slide_outline" not in ran


def test_slide_outline_skips_without_transcript():
    import tempfile
    from ytdrill.modules.slide_outline import SlideOutline
    ctx = Context(url="u", workdir=Path(tempfile.mkdtemp()), config={})
    SlideOutline({}).run(ctx)               # no transcript → graceful no-op
    assert getattr(ctx, "deck_md", "") == ""


def test_expand_series_input_reads_file():
    import tempfile
    from ytdrill.series import expand_series_input
    f = Path(tempfile.mkdtemp()) / "urls.txt"
    f.write_text("https://youtu.be/a\n# a comment\n\nhttps://youtu.be/b\n")
    urls, name = expand_series_input(str(f))
    assert urls == ["https://youtu.be/a", "https://youtu.be/b"]
    assert name == "urls"                   # file stem is the default series name


def test_run_series_collects_and_isolates_failure():
    import tempfile
    from ytdrill.series import run_series

    def fake_run(ctx, registry, **kw):
        n = ctx.url.rsplit("/", 1)[-1]
        if n == "bad":
            raise RuntimeError("boom")
        ctx.title = f"T{n}"; ctx.summary = f"## S\n\n{n}"; ctx.deck_md = f"## F\n\n- {n}"

    res = run_series(["https://x/a", "https://x/bad", "https://x/c"],
                     series_name="S", workdir=Path(tempfile.mkdtemp()),
                     config={}, registry={}, run=fake_run)
    assert [v["title"] for v in res] == ["Ta", "Tc"]        # 'bad' skipped
    assert res[0]["summary"].startswith("## S") and res[1]["deck"].startswith("## F")


def test_article_parses_through_textscan():
    """The built plain text must parse OPTIMALLY through the real downstream:
    bare headings → Sections, [n] → Citations. Skips if TEXTDRILL is absent."""
    import os
    import subprocess
    import tempfile
    src = Path.home() / "TEXTDRILL" / "src"
    if not (src / "textscan").is_dir():
        print("  (TEXTDRILL/textscan unavailable — downstream check skipped)")
        return
    from ytdrill.series import build_article_txt
    md = ("## Overview\n\nA result is shown [1].\n\n- alpha\n- beta\n\n"
          "## Method\n\nDetails here.\n\n## References\n\n[1] Paper X\n")
    art = build_article_txt("Test Series",
                            [{"title": "Episode One", "summary": md}])
    f = Path(tempfile.mkdtemp()) / "a.txt"
    f.write_text(art, encoding="utf-8")
    r = subprocess.run([sys.executable, "-m", "textscan", str(f),
                        "--emit", "docmodel"], capture_output=True, text=True,
                       env=dict(os.environ, PYTHONPATH=str(src)))
    types = [o["type"] for o in json.loads(r.stdout)["objects"]]
    assert types.count("Section") >= 3      # series + video + content headings
    assert "Citation" in types              # [1] recognised


if __name__ == "__main__":
    for fn in (test_pick_track_prefers_base_orig_over_translation,
               test_perplexity_chat_uses_responses_api,
               test_audio_download_extracts_in_its_own_session,
               test_caption_candidates_cover_every_language,
               test_transcript_falls_through_failed_tracks,
               test_transcript_probes_other_clients_before_giving_up,
               test_list_subs_reports_every_client_and_language,
               test_srt_clean, test_srt_matches_awk,
               test_json3_clean, test_emit_tiddler,
               test_build_video_html, test_emit_two_tiddlers_local,
               test_env_loader, test_extract_references,
               test_extra_fields_reach_tiddler,
               test_pgm_parse, test_dhash_hamming, test_slide_dedupe,
               test_hash_thumb_tolerates_bad_input,
               test_clean_frame_dir_removes_stale_files,
               test_subprocesses_do_not_eat_stdin,
               test_local_sidecar_discovery, test_local_id_deterministic,
               test_bibkey_of_shared_helper, test_emit_bibkey_override,
               test_audio_layer_format_excludes_video,
               test_run_plan_lazy_and_composable,
               test_asr_segments_from_whisper, test_asr_skips_without_audio,
               test_to_textscan_text_normalizes,
               test_build_article_txt_orders_and_titles,
               test_build_deck_txt_uses_deck_field,
               test_run_plan_slide_outline_after_summary,
               test_slide_outline_skips_without_transcript,
               test_expand_series_input_reads_file,
               test_run_series_collects_and_isolates_failure,
               test_article_parses_through_textscan):
        fn()
        print(f"ok  {fn.__name__}")
    print("all tests passed")
