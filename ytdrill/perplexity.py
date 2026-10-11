"""Shared Perplexity client (stdlib urllib) — used by summarize and
slide_outline so the key resolution and HTTP shape live in one place.

Since 2026-10 Sonar is served by the Agent API (``/v1/responses``);
``/chat/completions`` answers 403 agent_api_migration_required. Models are
provider-prefixed ("perplexity/sonar"); no ``preset``/``tools`` means no
web search, which is exactly what the transcript-only prompts want.

Secret order (NEVER hardcode — the repo is public): env PERPLEXITY_API_KEY,
then a configured ``secret_cmd`` whose stdout is the key.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import urllib.request

API_URL = "https://api.perplexity.ai/v1/responses"

NO_SEARCH = ("IMPORTANT: Do NOT search the web. Do NOT search the internet. "
             "Do NOT retrieve any external sources or URLs. "
             "Use ONLY the transcript and description provided in the user message.")


def resolve_key(secret_cmd: str = "") -> str:
    key = os.environ.get("PERPLEXITY_API_KEY", "").strip()
    if key:
        return key
    if secret_cmd:
        out = subprocess.run(shlex.split(secret_cmd), capture_output=True,
                             text=True, check=True).stdout.strip()
        if out:
            return out
    raise SystemExit(
        "No Perplexity key: set PERPLEXITY_API_KEY or configure the module's "
        "secret_cmd (e.g. 'apivault get perplexity').")


def model_id(model: str) -> str:
    """Legacy config names ("sonar") -> Agent API ids ("perplexity/sonar")."""
    return model if "/" in model else f"perplexity/{model}"


def chat(key: str, model: str, system: str, user: str, *,
         max_tokens: int = 2048, temperature: float = 0.2,
         timeout: int = 300) -> str:
    """One Agent API response; returns the assistant message text."""
    payload = {
        "model": model_id(model),
        "instructions": system,
        "input": user,
        "max_output_tokens": int(max_tokens),
        "temperature": float(temperature),
    }
    req = urllib.request.Request(
        API_URL, data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}",
                 "Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    parts = [c.get("text", "")
             for item in data.get("output") or []
             if item.get("type") == "message"
             for c in item.get("content") or []
             if c.get("type") == "output_text"]
    if not parts:
        raise RuntimeError(f"unexpected Perplexity response: "
                           f"{json.dumps(data)[:2000]}")
    return "".join(parts)
