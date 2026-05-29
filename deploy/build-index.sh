#!/bin/bash
# Publish the latest run as the site entrance, and (re)build the archive page.
#
#   out/index.html          ← a copy of the latest dashboard, so the bare domain
#                             (yalamanstockmarket.com) shows the most recent run
#                             directly instead of a landing page.
#   out/archive/index.html  ← a formatted, newest-first list of every archived
#                             snapshot. Served at /archive/ and linked from the
#                             dashboard header ("Past runs ↗").
#
# Run by cron after each weekly bot run. Idempotent: safe to run any time.
set -euo pipefail

OUT_DIR="${OUT_DIR:-/app/out}"
ARCHIVE_DIR="$OUT_DIR/archive"
DASHBOARD="$OUT_DIR/dashboard.html"
INDEX="$OUT_DIR/index.html"
ARCHIVE_INDEX="$ARCHIVE_DIR/index.html"

mkdir -p "$ARCHIVE_DIR"

# 1. Entrance = latest run. Copy the freshest dashboard to index.html so "/"
#    serves it immediately. (dashboard.html stays accessible directly too.)
if [[ -f "$DASHBOARD" ]]; then
    cp "$DASHBOARD" "$INDEX"
    echo "Published latest run -> $INDEX"
else
    echo "WARNING: $DASHBOARD not found; left $INDEX untouched" >&2
fi

# 2. Build the archive list. Each snapshot in archive/ becomes one row, newest
#    first. Links are relative to the archive dir (the page lives inside it).
#    Skip index.html itself so the page never lists its own URL.
#
#    We iterate basenames (not full paths) and quote the glob, so this is safe
#    even when OUT_DIR contains spaces — `for f in $(ls ...)` would word-split
#    such paths and emit garbage rows.
archive_items=""
shopt -s nullglob
snapshots=()
for f in "$ARCHIVE_DIR"/*.html; do
    n=$(basename "$f")
    [[ "$n" == "index.html" ]] && continue
    snapshots+=("$n")
done
shopt -u nullglob

if ((${#snapshots[@]})); then
    # Basenames are dates (YYYY-MM-DD.html, no spaces), so iterating the sorted
    # list is safe even when the directory path itself contains spaces.
    for name in $(printf '%s\n' "${snapshots[@]}" | sort -r); do
        archive_items+="    <li><a href=\"${name}\">${name%.html}</a></li>"$'\n'
    done
else
    archive_items='    <li><em>No archived runs yet.</em></li>'$'\n'
fi

cat > "$ARCHIVE_INDEX" <<HTML
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Archive — Yalman Stock Market Analyzer</title>
  <style>
    html { background: #fff; }
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
           max-width: 720px; margin: 3em auto; padding: 0 1em; color: #222;
           background: #fff; }
    h1 { font-size: 1.5em; margin-bottom: 0.2em; }
    .sub { color: #666; margin-top: 0; }
    a.latest { display: inline-block; margin: 1em 0 1.5em; padding: 0.6em 1em;
               background: #1a73e8; color: white; text-decoration: none;
               border-radius: 4px; }
    ul { padding-left: 1.2em; line-height: 1.9; }
    a { color: #1a73e8; }
  </style>
</head>
<body>
  <h1>Past runs</h1>
  <p class="sub">Weekly snapshots of the Yalman Stock Market Analyzer screen, newest first.</p>
  <a class="latest" href="../">&larr; View latest run</a>
  <ul>
$archive_items  </ul>
</body>
</html>
HTML

echo "Wrote $ARCHIVE_INDEX"
