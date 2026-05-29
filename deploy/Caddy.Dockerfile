# Tiny custom Caddy image with the Caddyfile baked in.
#
# Why this exists: Hostinger's Docker UI clones the repo to a /tmp/... path
# just for building, then runs containers from a different host path that
# only contains docker-compose.yml — not the rest of the repo. So a relative
# bind mount like `./deploy/Caddyfile:/etc/caddy/Caddyfile` resolves to a
# non-existent host path and Docker silently creates a directory there,
# which then fails to mount onto Caddy's file path. Baking it into the
# image sidesteps the issue entirely.
FROM caddy:2-alpine
COPY deploy/Caddyfile /etc/caddy/Caddyfile
