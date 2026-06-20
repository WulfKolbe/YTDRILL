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


if __name__ == "__main__":
    for fn in (test_srt_clean, test_srt_matches_awk,
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
               test_asr_segments_from_whisper, test_asr_skips_without_audio):
        fn()
        print(f"ok  {fn.__name__}")
    print("all tests passed")
