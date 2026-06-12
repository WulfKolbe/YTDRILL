#!/usr/bin/gawk -f
# yt_clean.awk — clean a YouTube SRT transcript into a single plain-text string.
#
# Usage:
#   gawk -f yt_clean.awk transcript.srt
#   gawk -f yt_clean.awk transcript.srt > clean.txt
#
# YouTube's rolling-caption format produces blocks like:
#
#   N
#   HH:MM:SS,mmm --> HH:MM:SS,mmm
#   ...last line of previous segment repeated...
#   ...new line(s)...
#
# Strategy: paragraph mode splits on blank lines → one SRT block per record.
# Field 1 = sequence number, field 2 = timestamp line, fields 3..NF = text.
# We strip \r, trim whitespace, and suppress consecutive duplicate lines
# before appending each unique line to the output string.

BEGIN {
    RS  = ""      # paragraph mode: records separated by blank lines
    FS  = "\n"    # fields are lines within each block
    prev = ""     # last line appended — used for dedup
    out  = ""     # accumulated transcript
}

{
    # Validate: field 2 must look like an SRT timestamp; skip non-SRT blocks.
    if ($2 !~ /[0-9]{2}:[0-9]{2}:[0-9]{2},[0-9]{3}[[:space:]]*-->[[:space:]]*[0-9]{2}:[0-9]{2}:[0-9]{2},[0-9]{3}/)
        next

    # Process text fields (everything after the timestamp line).
    for (i = 3; i <= NF; i++) {
        line = $i

        gsub(/\r/,              "",    line)   # remove ^M / CR
        gsub(/^[[:space:]]+/,  "",    line)   # ltrim
        gsub(/[[:space:]]+$/,  "",    line)   # rtrim

        if (line == "") continue               # skip blank lines within block

        # Only append if this line differs from the previous one.
        if (line != prev) {
            out  = (out == "") ? line : out " " line
            prev = line
        }
    }
}

END {
    if (out != "")
        print out
    else
        print "(no transcript text found)" > "/dev/stderr"
}
