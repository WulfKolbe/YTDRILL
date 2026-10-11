"""Summarize module — Perplexity Sonar via the shared ytdrill.perplexity client.

Secret resolution (in order, NEVER hardcoded — the repo will be public):
  1. env  PERPLEXITY_API_KEY
  2. config["modules"]["summarize"]["secret_cmd"], e.g. an apivault
     invocation ("apivault get perplexity"); stdout is the key.

Prompt design carries over the empirically tested bits of yt2tw.sh:
  - triple no-search instruction (system + user head + before howto)
  - transcript FIRST in the user message ("lost in the middle" mitigation)
  - howto template loaded from prompts/howto.md; the bash sed-strip of the
    unfilled "Inputs Provided" block is gone — the template simply no
    longer contains it.
"""
from __future__ import annotations

import logging
from pathlib import Path

from .base import BaseModule, Context
from ..perplexity import NO_SEARCH, chat, resolve_key

log = logging.getLogger("ytdrill")

SYSTEM_MSG = ("You are an expert academic writer and a proficient Markdown "
              "and LaTeX formatter. " + NO_SEARCH + " Your task is to "
              "transform the provided YouTube video transcript into a "
              "comprehensive, publication-ready Markdown document.")


class Summarize(BaseModule):
    name = "summarize"

    def run(self, ctx: Context) -> None:
        if not ctx.transcript:
            log.warning("    empty transcript — skipping summarization")
            return

        key = resolve_key(self.cfg.get("secret_cmd", ""))
        model = self.cfg.get("model", "sonar")
        howto = self._load_howto(ctx)

        comments_block = ""
        if ctx.comments:
            comments_block = ("## COMMENTS\n" + "\n".join(
                f"- {c['author']}: {c['text']}" for c in ctx.comments) + "\n\n")

        user_msg = (
            "IMPORTANT INSTRUCTION: Do not search the web. Do not search the "
            "internet. Use only the transcript and description text provided below.\n\n"
            f"## VIDEO URL\n{ctx.url}\n\n"
            f"## VIDEO TITLE\n{ctx.title}\n\n"
            f"## TRANSCRIPT\n{ctx.transcript}\n\n"
            f"## VIDEO DESCRIPTION\n{ctx.description}\n\n"
            f"{comments_block}"
            "## FORMATTING INSTRUCTIONS\n"
            "Do not search the web or the internet for any of the following "
            f"instructions.\n{howto}"
        )

        ctx.summary = chat(key, model, SYSTEM_MSG, user_msg,
                           max_tokens=int(self.cfg.get("max_tokens", 4096)),
                           temperature=float(self.cfg.get("temperature", 0.2)))

        ctx.summary_model = model
        (ctx.workdir / "summary.md").write_text(ctx.summary, encoding="utf-8")
        log.info("    summary: %d chars (model=%s)", len(ctx.summary), model)

    # ------------------------------------------------------------------
    def _load_howto(self, ctx: Context) -> str:
        p = Path(self.cfg.get("howto",
                              Path(__file__).parents[2] / "prompts" / "howto.md"))
        if p.is_file():
            return p.read_text(encoding="utf-8")
        log.warning("    howto template %s not found — using minimal default", p)
        return ("Produce: a title header, an abstract, sectioned summary "
                "following the argument structure, a bullet list of key "
                "claims, and a '## References' section listing every paper, "
                "book, person, or URL mentioned in the transcript or "
                "description (verbatim, no invented sources).")
