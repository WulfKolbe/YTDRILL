"""ExtractReferences — additive module (pdfdrill concept: new capability
as a new module, existing classes untouched).

The howto prompt instructs Sonar to emit a 'BibTeX Entries' section with
@type{key, ...} records and \\cite{key} usage in the text. This module
parses those records out of ctx.summary and exposes them so EmitTiddler
can attach them as fields:

    bibtex      — concatenated raw entries (verbatim, % Inferred kept)
    cite-keys   — space-separated bibkeys, e.g. "sciama1953 mach1883"

pdfdrill consumes cite-keys/bibtex to resolve and drill the referenced
documents; the bibkey here becomes pdfdrill's $Bibkey.

Parsing is brace-counting, not regex-greedy, so nested braces in titles
({DNA}, {Schr\"odinger}) survive.
"""
from __future__ import annotations

import logging
import re

from .base import BaseModule, Context

log = logging.getLogger("ytdrill")

_HEAD = re.compile(r"@([A-Za-z]+)\s*\{\s*([^,\s]+)\s*,")


def extract_bibtex(text: str) -> list[tuple[str, str, str]]:
    """Return [(entry_type, key, raw_entry)] for every balanced @type{...}."""
    out: list[tuple[str, str, str]] = []
    for m in _HEAD.finditer(text):
        start = m.start()
        brace_open = text.index("{", m.start())     # the { right after @type
        depth = 0
        for i in range(brace_open, len(text)):
            c = text[i]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    out.append((m.group(1).lower(), m.group(2),
                                text[start:i + 1]))
                    break
    return out


class ExtractReferences(BaseModule):
    name = "extract_references"

    def run(self, ctx: Context) -> None:
        if not ctx.summary:
            return
        entries = extract_bibtex(ctx.summary)
        if not entries:
            log.info("    no BibTeX entries found in summary")
            return
        # stash on config-free context attributes via dynamic fields dict;
        # EmitTiddler reads ctx.extra_fields if present (additive contract)
        extra = getattr(ctx, "extra_fields", None) or {}
        extra["bibtex"] = "\n\n".join(raw for _, _, raw in entries)
        extra["cite-keys"] = " ".join(key for _, key, _ in entries)
        ctx.extra_fields = extra  # type: ignore[attr-defined]
        log.info("    %d BibTeX entries: %s", len(entries),
                 extra["cite-keys"])
