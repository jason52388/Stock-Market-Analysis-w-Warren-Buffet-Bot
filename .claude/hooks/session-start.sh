#!/bin/bash
# SessionStart hook — bootstrap the warren-bot Python env so tests and the
# linter work in Claude Code on the web. Syncs the uv-managed virtualenv
# (incl. the `dev` extras: pytest, pytest-vcr, ruff) declared in pyproject.toml.
#
# Idempotent: `uv sync` is a no-op when the lockfile and .venv already match,
# and the container snapshot is cached after this completes.
set -euo pipefail

# Only meaningful in the remote web environment; locally the dev already has
# their own venv. Remove this guard to run everywhere.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "$CLAUDE_PROJECT_DIR"

# Resolve uv whether it's on PATH or installed to the default user location.
if ! command -v uv >/dev/null 2>&1; then
  export PATH="$HOME/.local/bin:$PATH"
fi

# Create/sync .venv from uv.lock, including dev tooling.
uv sync --extra dev

# Make the project console script + tools available for the rest of the session.
echo "export PATH=\"$CLAUDE_PROJECT_DIR/.venv/bin:\$PATH\"" >> "$CLAUDE_ENV_FILE"
