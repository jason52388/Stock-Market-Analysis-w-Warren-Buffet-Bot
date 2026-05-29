#!/bin/bash
# Rebuild out/index.html — a minimal landing page that links to the latest run
# and every archived snapshot, newest first.
#
# Run by cron after each weekly bot run. Idempotent: safe to run any time.
set -euo pipefail

OUT_DIR="${OUT_DIR:-/app/out}"
ARCHIVE_DIR="$OUT_DIR/archive"
INDEX="$OUT_DIR/index.html"

mkdir -p "$ARCHIVE_DIR"

# Build the archive list: each file in archive/ becomes one <li>, newest first.
archive_items=""
if compgen -G "$ARCHIVE_DIR/*.html" > /dev/null; then
    for f in $(ls -1 "$ARCHIVE_DIR"/*.html | sort -r); do
        date=$(basename "$f" .html)
        archive_items+="    <li><a href=\"archive/${date}.html\">${date}</a></li>"$'\n'
    done
else
    archive_items='    <li><em>No archived runs yet.</em></li>'$'\n'
fi

cat > "$INDEX" <<HTML
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Warren Buffett Bot</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
           max-width: 720px; margin: 3em auto; padding: 0 1em; color: #222; }
    h1 { font-size: 1.5em; margin-bottom: 0.2em; }
    .sub { color: #666; margin-top: 0; }
    a.latest { display: inline-block; margin: 1em 0; padding: 0.6em 1em;
               background: #1a73e8; color: white; text-decoration: none;
               border-radius: 4px; }
    ul { padding-left: 1.2em; line-height: 1.8; }
    code { background: #f4f4f4; padding: 0.1em 0.3em; border-radius: 3px; }
  </style>
</head>
<body>
  <h1>Warren Buffett Bot</h1>
  <p class="sub">Weekly screen of S&amp;P 500 + ADRs against Buffett/Munger criteria.</p>
  <a class="latest" href="preview.html">View latest run &rarr;</a>
  <h2>Archive</h2>
  <ul>
$archive_items  </ul>
</body>
</html>
HTML

echo "Wrote $INDEX"
