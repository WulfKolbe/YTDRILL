"""Minimal .env support — stdlib only (pdfdrill convention: no deps
beyond what the pipeline fundamentally needs).

Resolution order for any secret (highest wins):
  1. real process environment
  2. .env file (search: --env path > workdir > project root > cwd)
  3. config secret_cmd (apivault)

.env grammar: KEY=VALUE lines, '#' comments, optional 'export ' prefix,
single/double quotes stripped. No interpolation — keep it dumb and safe.
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path

log = logging.getLogger("ytdrill")

_LINE = re.compile(r"""^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$""")


def parse_env(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _LINE.match(line)
        if not m:
            continue
        key, val = m.group(1), m.group(2)
        # strip one layer of matching quotes; drop trailing comment if unquoted
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "'\"":
            val = val[1:-1]
        else:
            val = val.split(" #", 1)[0].rstrip()
        out[key] = val
    return out


def load_env(explicit: Path | None = None,
             search: list[Path] | None = None) -> dict[str, str]:
    """Load the first .env found; inject keys NOT already in os.environ.
    Returns the merged view of loaded keys (for logging/tests)."""
    candidates: list[Path] = []
    if explicit:
        candidates.append(explicit)
    for d in (search or []):
        candidates.append(Path(d) / ".env")

    for p in candidates:
        if p and p.is_file():
            loaded = parse_env(p.read_text(encoding="utf-8"))
            applied = {}
            for k, v in loaded.items():
                if k not in os.environ:        # real env always wins
                    os.environ[k] = v
                    applied[k] = v
            log.debug("    .env: %s (%d keys, %d applied)",
                      p, len(loaded), len(applied))
            return loaded
    return {}
