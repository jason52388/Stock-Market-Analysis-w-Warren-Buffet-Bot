"""Settings loader. Expands ${VAR} from the environment so secrets stay out of YAML."""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml

_ENV_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)\}")


def _expand(value: Any) -> Any:
    if isinstance(value, str):
        return _ENV_PATTERN.sub(lambda m: os.environ.get(m.group(1), ""), value)
    if isinstance(value, dict):
        return {k: _expand(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand(v) for v in value]
    return value


def load_settings(path: str | Path = "config/settings.yaml") -> dict[str, Any]:
    p = Path(path)
    with p.open() as f:
        raw = yaml.safe_load(f)
    return _expand(raw)


def repo_root() -> Path:
    """Repo root = parent of the `src` directory containing this file."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").exists():
            return parent
    return Path.cwd()
