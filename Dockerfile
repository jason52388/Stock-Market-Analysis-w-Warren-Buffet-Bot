FROM python:3.12-slim

# Belt-and-suspenders TZ default — docker-compose.yml passes TZ in via env, but
# this protects standalone `docker run` invocations (debugging, one-off scripts)
# from silently falling back to UTC and shifting the cron schedule by hours.
ENV TZ=America/New_York

# System deps: cron for scheduling, tzdata for TZ env var, ca-certs for SMTP TLS
RUN apt-get update && apt-get install -y --no-install-recommends \
        cron \
        tzdata \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install uv (matches local dev + GH Actions workflow)
RUN pip install --no-cache-dir uv

WORKDIR /app

# Install Python deps first for layer caching
COPY pyproject.toml ./
COPY src/ ./src/
RUN uv pip install --system -e .

# Config + universe files (these change rarely)
COPY config/ ./config/

# Deploy scripts (build-index.sh is invoked by the cron line at /app/deploy/)
COPY deploy/ ./deploy/

# Cron + entrypoint
COPY deploy/crontab /etc/cron.d/warren-bot
COPY deploy/entrypoint.sh /entrypoint.sh
RUN chmod 0644 /etc/cron.d/warren-bot \
 && chmod 0755 /entrypoint.sh /app/deploy/build-index.sh \
 && touch /var/log/warren.log

# .cache and out are bind-mounted from the host; create empty mount points
RUN mkdir -p /app/.cache /app/out/archive

ENTRYPOINT ["/entrypoint.sh"]
