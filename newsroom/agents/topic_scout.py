"""
Topic Scout Agent

Responsibilities:
1. Read recent RSS entries to get a pool of candidate topics
2. Pick the single most newsworthy topic via LLM judgment
3. Search for additional context on that topic
4. Return a structured Topic ready for the Journalist
"""

from __future__ import annotations

from typing import Optional
from uuid import uuid4

import structlog
from pydantic import BaseModel, Field

from agents.base import BaseAgent
from config.settings import Settings
from models.schemas import ArticleStatus, NewsroomState, SearchResult, Topic
from tools.rss import RssEntry, RssReader
from tools.search import SearchTool

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Structured output schemas (LLM → Python)
# ---------------------------------------------------------------------------


class TopicSelection(BaseModel):
    """What the LLM returns when asked to select and describe a topic."""

    selected_title: str = Field(description="Title of the chosen RSS item")
    reasoning: str = Field(description="Why this topic is the most newsworthy")
    search_query: str = Field(description="Best search query to find more context")
    category: str = Field(
        default="general",
        description="Topic category: politics, technology, science, economy, culture, etc.",
    )
    summary: str = Field(description="2-3 sentence summary of what this story is about")


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


class TopicScoutAgent(BaseAgent):
    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self._rss = RssReader(
            feeds=settings.rss_feeds,
            max_age_hours=settings.rss_max_age_hours,
        )
        self._search = SearchTool(
            tavily_api_key=settings.tavily_api_key.get_secret_value() if settings.has_tavily() else None,
            max_results=settings.search_max_results,
            timeout=settings.search_timeout_seconds,
        )

    def run(self, state: NewsroomState) -> dict:
        """
        LangGraph node function.
        Returns partial state dict with `topic` and `status` updated.
        """
        return self._timed_run("topic_scout", self._execute, state)

    def _execute(self, state: NewsroomState) -> dict:
        errors = list(state.get("errors", []))

        # 1. Fetch RSS entries
        entries = self._rss.fetch_recent()
        if not entries:
            log.warning("scout.no_rss_entries")
            errors.append("No RSS entries found — check feed URLs and connectivity")
            return {"status": ArticleStatus.FAILED.value, "errors": errors}

        # Cap at 20 entries to keep the prompt size reasonable
        candidates = entries[:20]

        # 2. Ask LLM to pick the best topic
        selection = self._select_topic(candidates)
        if selection is None:
            errors.append("LLM failed to select a topic")
            return {"status": ArticleStatus.FAILED.value, "errors": errors}

        # Find the matching entry for its URL
        chosen_entry = self._find_entry(selection.selected_title, candidates)
        source_url = chosen_entry.url if chosen_entry else ""

        # 3. Search for more context
        search_results = self._search.search(selection.search_query, topic="news")

        topic = Topic(
            id=uuid4(),
            title=selection.selected_title,
            query=selection.search_query,
            summary=selection.summary,
            sources=search_results,
            category=selection.category,
        )

        log.info(
            "scout.topic_selected",
            title=topic.title,
            category=topic.category,
            sources=len(search_results),
        )

        return {
            "topic": topic,
            "status": ArticleStatus.WRITING.value,
            "errors": errors,
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _select_topic(self, entries: list[RssEntry]) -> Optional[TopicSelection]:
        lang = self._settings.language_name()
        headlines = "\n".join(
            f"{i+1}. [{e.source_name}] {e.title}" for i, e in enumerate(entries)
        )

        system = (
            "You are an experienced news editor. "
            f"Your goal is to select the single most newsworthy story to write about in {lang}. "
            "Prefer stories that are recent, have broad impact, and are based on verifiable facts. "
            "Avoid opinion pieces, sponsored content, and trivial entertainment news."
        )
        user = (
            f"Here are the latest headlines:\n\n{headlines}\n\n"
            "Select the ONE story that deserves in-depth coverage today and explain why."
        )

        try:
            llm = self.structured_llm(TopicSelection)
            from langchain_core.messages import HumanMessage, SystemMessage

            result = llm.invoke([SystemMessage(content=system), HumanMessage(content=user)])
            return result
        except Exception as e:
            log.error("scout.llm_error", error=str(e))
            return None

    @staticmethod
    def _find_entry(title: str, entries: list[RssEntry]) -> Optional[RssEntry]:
        title_lower = title.lower()
        for entry in entries:
            if entry.title.lower() in title_lower or title_lower in entry.title.lower():
                return entry
        return None
