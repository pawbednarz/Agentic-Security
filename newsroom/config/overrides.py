"""
Runtime config overrides stored in data/config_overrides.json.

These values take precedence over .env settings and can be modified
via the web GUI without restarting the server. Only non-secret,
operational fields are exposed here — never API keys.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger(__name__)

_OVERRIDES_PATH = Path(__file__).parent.parent / "data" / "config_overrides.json"

# Allowlist: only these fields can be changed at runtime via the GUI
OVERRIDABLE_FIELDS = {
    "newsroom_language",
    "newsroom_min_words",
    "newsroom_max_words",
    "newsroom_max_revisions",
    "newsroom_min_fact_score",
    "rss_feeds",
    "llm_model",
}


def load_overrides() -> dict[str, Any]:
    if not _OVERRIDES_PATH.exists():
        return {}
    try:
        return json.loads(_OVERRIDES_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        log.warning("overrides.load_failed", error=str(e))
        return {}


def save_overrides(overrides: dict[str, Any]) -> None:
    safe = {k: v for k, v in overrides.items() if k in OVERRIDABLE_FIELDS}
    _OVERRIDES_PATH.parent.mkdir(parents=True, exist_ok=True)
    _OVERRIDES_PATH.write_text(
        json.dumps(safe, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    log.info("overrides.saved", keys=list(safe.keys()))


def apply_overrides(settings):
    """Return a new Settings instance with GUI overrides applied."""
    overrides = load_overrides()
    if not overrides:
        return settings
    return settings.model_copy(update=overrides)
