#!/bin/bash
# yt2tw.sh — download a YouTube video, extract transcript + description,
#             clean the SRT, and summarise via Perplexity Sonar API.
#
# Usage:  ./yt2tw.sh <youtube-url>

set -euo pipefail          # exit on error, unset vars, pipe failures

WORKDIR=/home/wkolbe/Downloads
PERPLEXITY_API_KEY="REDACTED-ROTATE-THIS-KEY"
PERPLEXITY_MODEL="sonar"
AWK_SCRIPT="$(dirname "$0")/clean_transcript.awk"

cd "$WORKDIR"

# ---------------------------------------------------------------------------
download_video() {
    local url="$1"
    echo "==> Downloading video + audio…"
    rm -f audio.m4a video.mp4
    yt-dlp -f 'bestvideo[ext=mp4]' "$url" -o 'video.mp4'
    yt-dlp -f 'bestaudio[ext=m4a]'  "$url" -o 'audio.m4a'
    echo "    Audio/video downloaded."
}

# ---------------------------------------------------------------------------
get_transcript() {
    local url="$1"
    echo "==> Fetching auto-generated subtitles…"

    # Remove any leftover subtitle and transcript files so globs are unambiguous.
    rm -f transcript.srt cleaned_transcript.md
    rm -f ./*.srt ./*.vtt 2>/dev/null || true

    # No --sub-lang: fetch whatever language the video is in (original language).
    # --retries 5 handles transient 429 rate-limit errors automatically.
    yt-dlp --skip-download \
           --write-auto-sub \
           --convert-subs srt \
           --retries 5 \
           "$url"

    # Prefer .srt; fall back to .vtt (yt-dlp sometimes skips conversion).
    local sub_file srt_file
    srt_file=$(ls ./*.srt 2>/dev/null | head -1)

    if [[ -n "$srt_file" ]]; then
        echo "    Found SRT: $srt_file"
        cp "$srt_file" transcript.srt

    else
        sub_file=$(ls ./*.vtt 2>/dev/null | head -1)
        if [[ -z "$sub_file" ]]; then
            echo "ERROR: no .srt or .vtt subtitle file found after yt-dlp." >&2
            exit 1
        fi
        echo "    Found VTT: $sub_file — converting to SRT via ffmpeg…"
        ffmpeg -y -i "$sub_file" transcript.srt 2>/dev/null
        if [[ ! -s transcript.srt ]]; then
            echo "ERROR: ffmpeg VTT→SRT conversion produced an empty file." >&2
            exit 1
        fi
    fi

    echo "    Cleaning transcript…"
    awk -f "$AWK_SCRIPT" transcript.srt > cleaned_transcript.md
    echo "    Saved cleaned_transcript.md"
}

# ---------------------------------------------------------------------------
get_description() {
    local url="$1"
    echo "==> Fetching video description…"
    rm -f description.md
    rm -f ./*.description 2>/dev/null || true

    yt-dlp --skip-download --write-description "$url"

    local desc_file
    desc_file=$(ls ./*.description 2>/dev/null | head -1)

    if [[ -z "$desc_file" ]]; then
        echo "WARNING: no .description file found; description.md will be empty." >&2
        touch description.md
        return
    fi

    echo "    Found description: $desc_file"
    cp "$desc_file" description.md
    echo "    Saved description.md"
}

# ---------------------------------------------------------------------------
call_perplexity() {
    local url="$1"
    echo "==> Calling Perplexity API (model: ${PERPLEXITY_MODEL})…"

    # ------------------------------------------------------------------ #
    # Read source files
    # ------------------------------------------------------------------ #
    local transcript description howto
    transcript=$(cat cleaned_transcript.md)
    description=$(cat description.md)

    # Strip the unfilled "Inputs Provided:" block from howto.txt so the
    # model does not see empty required fields — we supply them ourselves.
    howto=$(sed '/\*\*Inputs Provided:\*\*/,/3\. \*\*References:\*\*/d' howto.txt)

    # ------------------------------------------------------------------ #
    # System message — role + hard no-search constraint (x3 as tested)
    # ------------------------------------------------------------------ #
    local system_msg
    system_msg="You are an expert academic writer and a proficient Markdown \
and LaTeX formatter. \
IMPORTANT: Do NOT search the web. Do NOT search the internet. \
Do NOT retrieve any external sources or URLs. \
Use ONLY the transcript and description provided in the user message. \
Your task is to transform the provided YouTube video transcript into a \
comprehensive, publication-ready Markdown document."

    # ------------------------------------------------------------------ #
    # User message — transcript FIRST to avoid "lost in the middle" effect,
    # then description, then formatting instructions.
    # Three explicit no-search reminders are included as tested.
    # ------------------------------------------------------------------ #
    local user_msg
    user_msg="IMPORTANT INSTRUCTION: Do not search the web. Do not search the internet. Use only the transcript and description text provided below.

## VIDEO URL
${url}

## TRANSCRIPT
${transcript}

## VIDEO DESCRIPTION
${description}

## FORMATTING INSTRUCTIONS
Do not search the web or the internet for any of the following instructions.
${howto}"

    # ------------------------------------------------------------------ #
    # Build JSON — jq --arg handles ALL escaping (newlines, quotes, etc.)
    # ------------------------------------------------------------------ #
    local json_payload
    json_payload=$(jq -n \
        --arg model      "$PERPLEXITY_MODEL" \
        --arg system_msg "$system_msg" \
        --arg user_msg   "$user_msg" \
        '{
            model: $model,
            messages: [
                { role: "system", content: $system_msg },
                { role: "user",   content: $user_msg   }
            ],
            max_tokens: 4096,
            temperature: 0.2
        }')

    echo "$json_payload" > payload.json
    echo "    Payload written to payload.json ($(wc -c < payload.json) bytes)"

    # ------------------------------------------------------------------ #
    # API call
    # ------------------------------------------------------------------ #
    local response
    response=$(curl -s -X POST "https://api.perplexity.ai/chat/completions" \
        -H "Authorization: Bearer ${PERPLEXITY_API_KEY}" \
        -H "Content-Type: application/json" \
        -d "$json_payload")

    echo "$response" > response.json

    # ------------------------------------------------------------------ #
    # Extract answer or show a useful error
    # ------------------------------------------------------------------ #
    if echo "$response" | jq -e '.choices[0].message.content' > /dev/null 2>&1; then
        echo "$response" | jq -r '.choices[0].message.content' | tee summary.md
        echo ""
        echo "    Summary saved to summary.md"
    else
        echo "ERROR: unexpected API response:" >&2
        echo "$response" | jq . >&2
        exit 1
    fi
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if [[ $# -eq 0 ]]; then
    echo "Usage: $0 <youtube-url>" >&2
    exit 1
fi

URL="$1"
download_video  "$URL"
get_transcript  "$URL"
get_description "$URL"
call_perplexity "$URL"
