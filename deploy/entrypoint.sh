#!/bin/bash
# Container entrypoint.
#
# Cron does NOT inherit Docker's env vars — it starts jobs with a near-empty
# environment. We dump the current env to /etc/environment (which cron *does*
# read) so the weekly run can see GMAIL_*, NOTION_*, TZ, etc.
set -euo pipefail

# Use python to dump the env safely: keys come from os.environ verbatim, values
# are double-quote-escaped. This handles values containing '=' or '"' (rare but
# legal) which a naive `sed 's/=/="/` chain would mangle.
python3 - <<'PY' > /etc/environment
import os
for k, v in os.environ.items():
    # PAM's pam_env reads "KEY=\"VALUE\"" — escape \ and " in the value.
    safe = v.replace("\\", "\\\\").replace('"', '\\"')
    print(f'{k}="{safe}"')
PY

# Make sure the log file exists so `tail -f` works from `docker compose logs`.
touch /var/log/warren.log

# Stream cron's job output as container logs. Cron writes job stdout/stderr to
# /var/log/warren.log (see crontab); tailing it in the background lets
# `docker compose logs bot` show weekly runs in real time.
tail -F /var/log/warren.log &

# PID 1 is cron in the foreground so the container stays alive.
exec cron -f
