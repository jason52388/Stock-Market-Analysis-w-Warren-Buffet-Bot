#!/bin/bash
# Container entrypoint.
#
# Cron does NOT inherit Docker's env vars — it starts jobs with a near-empty
# environment. We dump the current env to /etc/environment (which cron *does*
# read) so the weekly run can see GMAIL_*, NOTION_*, TZ, etc. Quoting protects
# values that contain spaces or special chars (app passwords often do).
set -euo pipefail

printenv | sed 's/^\(.*\)=\(.*\)$/\1="\2"/' > /etc/environment

# Make sure the log file exists so `tail -f` works from `docker compose logs`.
touch /var/log/warren.log

# Stream cron's job output as container logs. Cron writes job stdout/stderr to
# /var/log/warren.log (see crontab); tailing it in the background lets
# `docker compose logs bot` show weekly runs in real time.
tail -F /var/log/warren.log &

# PID 1 is cron in the foreground so the container stays alive.
exec cron -f
