"""Load config.yaml and .env. Secrets never live in config.yaml; they come from .env."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent


def load_config(path: Path | None = None) -> dict[str, Any]:
    path = path or ROOT / "config.yaml"
    with open(path) as f:
        return yaml.safe_load(f)


def load_env(path: Path | None = None) -> None:
    """Read KEY=value lines from .env into os.environ without overriding existing values."""
    path = path or ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))
