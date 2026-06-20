#!/bin/bash
# Batch-run ytdrill over every .mkv in testdata/, one workdir per video.
# Re-runnable: videos whose workdir already holds a tiddler are skipped.
OUT="$HOME/Downloads/yt2tw-batch"
mkdir -p "$OUT"
cd "$(dirname "$0")"

total=$(find testdata -name '*.mkv' | wc -l)
i=0
find testdata -name '*.mkv' -print0 | sort -z | while IFS= read -r -d '' f; do
  i=$((i+1))
  stem=$(basename "$f" .mkv)
  d="$OUT/$(printf '%03d' "$i")"
  if ls "$d"/*_video_0001.json >/dev/null 2>&1; then
    echo "SKIP [$i/$total] $stem"
    continue
  fi
  mkdir -p "$d"
  # </dev/null: children must never read the find-pipe feeding this loop
  if timeout 3600 python3 -m ytdrill --slides --workdir "$d" "$f" \
       > "$d/run.log" 2>&1 < /dev/null; then
    echo "OK   [$i/$total] $stem"
  else
    echo "FAIL [$i/$total] $stem (exit $?, see $d/run.log)"
  fi
done
echo "BATCH DONE"
