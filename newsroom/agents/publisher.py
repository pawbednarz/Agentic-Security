"""
Publisher Agent

Responsibilities:
1. Receive the verified, edited article
2. Save it as a Markdown file with YAML front-matter
3. Write a JSON manifest (index of all published articles)
4. Return the path of the saved file

The publisher is the last agent in the pipeline.
It should never fail — if writing files fails, it logs and returns an error status.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import structlog

from agents.base import BaseAgent
from config.settings import Settings
from models.schemas import Article, ArticleStatus, NewsroomState

log = structlog.get_logger(__name__)

MANIFEST_FILENAME = "manifest.json"


class PublisherAgent(BaseAgent):
    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self._output_dir = Path(settings.newsroom_output_dir)

    def run(self, state: NewsroomState) -> dict:
        return self._timed_run("publisher", self._execute, state)

    def _execute(self, state: NewsroomState) -> dict:
        errors = list(state.get("errors", []))
        article = state.get("edited_article")

        if article is None:
            errors.append("Publisher: no edited article found")
            return {"status": ArticleStatus.FAILED.value, "errors": errors}

        try:
            self._output_dir.mkdir(parents=True, exist_ok=True)
            filepath = self._save_article(article)
            self._update_manifest(article, filepath)
        except OSError as e:
            log.error("publisher.io_error", error=str(e))
            errors.append(f"Publisher IO error: {e}")
            return {"status": ArticleStatus.FAILED.value, "errors": errors}

        log.info(
            "publisher.published",
            path=str(filepath),
            title=article.title,
            word_count=article.word_count,
        )

        return {
            "published_path": str(filepath),
            "status": ArticleStatus.PUBLISHED.value,
            "errors": errors,
        }

    # ------------------------------------------------------------------
    # File operations
    # ------------------------------------------------------------------

    def _save_article(self, article: Article) -> Path:
        slug = self._slugify(article.title)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename = f"{timestamp}_{slug}.md"
        filepath = self._output_dir / filename

        filepath.write_text(article.to_markdown(), encoding="utf-8")
        return filepath

    def _update_manifest(self, article: Article, filepath: Path) -> None:
        """
        Maintains a JSON index of all published articles.
        Appends the new entry without rewriting existing ones.
        """
        manifest_path = self._output_dir / MANIFEST_FILENAME

        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                manifest = {"articles": []}
        else:
            manifest = {"articles": []}

        manifest["articles"].append(
            {
                "id": str(article.id),
                "title": article.title,
                "file": filepath.name,
                "word_count": article.word_count,
                "revision": article.revision,
                "published_at": datetime.now(timezone.utc).isoformat(),
            }
        )

        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _slugify(text: str) -> str:
        """
        Converts a title to a filesystem-safe slug.
        e.g. "Wielki pożar w Krakowie!" → "wielki-pozar-w-krakowie"
        """
        # Transliterate common Polish characters
        pl_map = str.maketrans(
            "ąćęłńóśźżĄĆĘŁŃÓŚŹŻ",
            "acelnoszzACELNOSZZ",
        )
        text = text.translate(pl_map)
        text = text.lower()
        text = re.sub(r"[^\w\s-]", "", text)
        text = re.sub(r"[\s_]+", "-", text)
        text = text.strip("-")
        return text[:60]
