"""
Configuration API — runtime settings and prompt overrides.

Endpoints:
  GET    /api/v1/config                    → current effective settings (no secrets)
  PUT    /api/v1/config                    → save settings overrides to data/config_overrides.json
  DELETE /api/v1/config                    → reset all overrides to .env defaults
  GET    /api/v1/prompts                   → default + override prompts for all agents
  PUT    /api/v1/prompts                   → save a prompt override
  GET    /api/v1/articles/{filename}/content → raw markdown content of a published article
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.auth import verify_token
from config.overrides import OVERRIDABLE_FIELDS, load_overrides, save_overrides
from config.prompts import DEFAULTS, PROMPT_META, load_prompt_overrides, save_prompt_overrides
from config.settings import Settings, get_settings

router = APIRouter(prefix="/api/v1", tags=["config"])

_SAFE_FILENAME = re.compile(r"^[\w\-\.]+$")


# ── Settings ──────────────────────────────────────────────────────────────


class ConfigUpdate(BaseModel):
    newsroom_language: Optional[str] = None
    newsroom_min_words: Optional[int] = Field(None, ge=200, le=2000)
    newsroom_max_words: Optional[int] = Field(None, ge=300, le=3000)
    newsroom_max_revisions: Optional[int] = Field(None, ge=0, le=5)
    newsroom_min_fact_score: Optional[float] = Field(None, ge=0.0, le=1.0)
    rss_feeds: Optional[list[str]] = None
    llm_model: Optional[str] = None


@router.get("/config", dependencies=[Depends(verify_token)])
async def get_config(settings: Settings = Depends(get_settings)) -> dict:
    """Returns the current effective settings merged from .env + overrides."""
    overrides = load_overrides()
    return {
        "newsroom_language": overrides.get("newsroom_language", settings.newsroom_language),
        "newsroom_min_words": overrides.get("newsroom_min_words", settings.newsroom_min_words),
        "newsroom_max_words": overrides.get("newsroom_max_words", settings.newsroom_max_words),
        "newsroom_max_revisions": overrides.get("newsroom_max_revisions", settings.newsroom_max_revisions),
        "newsroom_min_fact_score": overrides.get("newsroom_min_fact_score", settings.newsroom_min_fact_score),
        "rss_feeds": overrides.get("rss_feeds", settings.rss_feeds),
        "llm_model": overrides.get("llm_model", settings.llm_model),
        "overridden_keys": list(overrides.keys()),
    }


@router.put("/config", dependencies=[Depends(verify_token)])
async def update_config(body: ConfigUpdate) -> dict:
    """Merges provided values into the overrides file."""
    current = load_overrides()
    current.update(body.model_dump(exclude_none=True))
    save_overrides(current)
    return {"saved": True, "overridden_keys": list(current.keys())}


@router.delete("/config", dependencies=[Depends(verify_token)])
async def reset_config() -> dict:
    """Deletes all overrides — settings revert to .env values."""
    save_overrides({})
    return {"reset": True}


# ── Prompts ───────────────────────────────────────────────────────────────


class PromptUpdate(BaseModel):
    key: str
    override: Optional[str] = None  # empty/None = revert to default


@router.get("/prompts", dependencies=[Depends(verify_token)])
async def get_prompts() -> list[dict]:
    """Returns default + override for every agent prompt."""
    overrides = load_prompt_overrides()
    return [
        {
            "key": key,
            "label": meta["label"],
            "default": DEFAULTS[key],
            "override": overrides.get(key),
            "variables": meta["variables"],
        }
        for key, meta in PROMPT_META.items()
    ]


@router.put("/prompts", dependencies=[Depends(verify_token)])
async def update_prompt(body: PromptUpdate) -> dict:
    """Saves or clears an override for a single prompt key."""
    if body.key not in DEFAULTS:
        raise HTTPException(status_code=400, detail=f"Unknown prompt key: {body.key}")

    current = load_prompt_overrides()
    if body.override and body.override.strip():
        current[body.key] = body.override.strip()
    else:
        current.pop(body.key, None)

    save_prompt_overrides(current)
    return {"saved": True, "key": body.key, "overridden": bool(current.get(body.key))}


# ── Article content ───────────────────────────────────────────────────────


@router.get("/articles/{filename}/content", dependencies=[Depends(verify_token)])
async def get_article_content(
    filename: str,
    settings: Settings = Depends(get_settings),
) -> dict:
    """Returns the raw Markdown content of a published article."""
    if not _SAFE_FILENAME.match(filename):
        raise HTTPException(status_code=400, detail="Invalid filename")

    path = Path(settings.newsroom_output_dir) / filename
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Article not found")

    try:
        return {"filename": filename, "content": path.read_text(encoding="utf-8")}
    except OSError:
        raise HTTPException(status_code=500, detail="Could not read article")
