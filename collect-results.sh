#!/bin/bash
# Copy ytdrill batch results next to the original videos as sidecar files:
#   <stem>.tiddler.json   TiddlyWiki import file
#   <stem>.summary.md     Sonar summary (only when a transcript existed)
#   <stem>.slides.pdf     searchable OCR'd slide deck
# Mapping is by tiddler caption (= video stem), NOT by batch index, so it
# survives reordered/partial/interrupted batch runs. Afterwards lists every
# video that still has no tiddler sidecar.
OUT="$HOME/Downloads/yt2tw-batch"
cd "$(dirname "$0")"

python3 - "$OUT" <<'EOF'
import json, shutil, sys
from pathlib import Path

out = Path(sys.argv[1])
testdata = Path("testdata")
videos = {p.stem: p for p in testdata.rglob("*.mkv")}

copied = mismatched = 0
for tiddler in sorted(out.glob("*/*_video_0001.json")):
    caption = json.load(open(tiddler))[0].get("caption", "")
    video = videos.get(caption)
    if video is None:
        print(f"NO SOURCE for workdir {tiddler.parent.name}: {caption!r}")
        mismatched += 1
        continue
    stem = video.with_suffix("")
    summary = tiddler.parent / "summary.md"
    if summary.is_file():
        shutil.copy2(summary, f"{stem}.summary.md")
    pdfs = list(tiddler.parent.glob("*_slides.pdf"))
    data = json.load(open(tiddler))
    if pdfs:
        sidecar_pdf = Path(f"{stem}.slides.pdf")
        shutil.copy2(pdfs[0], sidecar_pdf)
        # slides link in the tiddler points at the workdir; retarget it
        # (and the slides-pdf field) to the sidecar next to the video
        old_uri = pdfs[0].resolve().as_uri()
        for t in data:
            if "text" in t:
                t["text"] = t["text"].replace(old_uri,
                                              sidecar_pdf.resolve().as_uri())
            if t.get("slides-pdf"):
                t["slides-pdf"] = sidecar_pdf.name
    json.dump(data, open(f"{stem}.tiddler.json", "w"),
              ensure_ascii=False, indent=1)
    copied += 1

todo = [p for s, p in sorted(videos.items())
        if not Path(f"{p.with_suffix('')}.tiddler.json").is_file()]
for p in todo:
    print(f"MISSING {p.name}")
print(f"collected: {copied}, no-source: {mismatched}, missing: {len(todo)}")
EOF
