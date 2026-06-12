#!/bin/bash
# Copy yt2tw batch results next to the original videos as sidecar files:
#   <stem>.tiddler.json   TiddlyWiki import file
#   <stem>.summary.md     Sonar summary (only when a transcript existed)
#   <stem>.slides.pdf     searchable OCR'd slide deck
# Mapping uses the SAME find|sort enumeration as batch-testdata.sh.
OUT="$HOME/Downloads/yt2tw-batch"
cd "$(dirname "$0")"

i=0; copied=0; missing=0
find testdata -name '*.mkv' -print0 | sort -z | while IFS= read -r -d '' f; do
  i=$((i+1))
  d="$OUT/$(printf '%03d' "$i")"
  stem="${f%.mkv}"
  tiddler=$(ls "$d"/*_video_0001.json 2>/dev/null | head -1)
  if [ -z "$tiddler" ]; then
    echo "MISSING [$i] $(basename "$f")"
    missing=$((missing+1))
    continue
  fi
  # safety: tiddler caption must match the video stem
  if ! python3 - "$tiddler" "$(basename "$stem")" <<'EOF'
import json, sys
t = json.load(open(sys.argv[1]))[0]
sys.exit(0 if t.get("caption") == sys.argv[2] else 1)
EOF
  then
    echo "MISMATCH [$i] $(basename "$f") vs $(basename "$tiddler")"
    continue
  fi
  cp "$tiddler" "$stem.tiddler.json"
  [ -f "$d/summary.md" ] && cp "$d/summary.md" "$stem.summary.md"
  pdf=$(ls "$d"/*_slides.pdf 2>/dev/null | head -1)
  [ -n "$pdf" ] && cp "$pdf" "$stem.slides.pdf"
  copied=$((copied+1))
done
echo "collected: $copied, missing: $missing"
