"""Tiny .env loader shared by main.py, web/server.py, and CLI commands.

Avoids a hard `python-dotenv` dependency for the small set of vars we need
(LANDFIRE_EMAIL, LANDFIRE_VERSION).
"""

from __future__ import annotations

import os
from pathlib import Path


def load_env_file(path: Path) -> None:
    """Load simple `KEY=VALUE` pairs from `path` into the process environment.

    - Lines starting with `#` are ignored.
    - Surrounding single/double quotes on the value are stripped.
    - Existing environment variables are NOT overridden (`os.environ.setdefault`).
    - Missing or unreadable files are silent no-ops — callers should not fail
      when `.env` is absent.
    """
    if not path.exists():
        return
    try:
        text = path.read_text()
    except OSError:
        return
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
