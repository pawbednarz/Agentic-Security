"""
Journalist Agent

Responsibilities:
1. Read the topic and its search results
2. Write a well-structured article using structured output
3. Produce a complete Article (lead, body, conclusion, sources)

Key design choices:
- Uses `.with_structured_output()` — the LLM returns a typed object,
  no fragile string parsing needed.
- External web content is wrapped in `<external_content>` tags to
  guard against prompt injection.
- The system prompt enforces journalistic standards explicitly.
"""

from __future__ import annotations

import structlog
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from agents.base import BaseAgent
from config.prompts import get_prompt
from config.settings import Settings
from models.schemas import Article, ArticleStatus, NewsroomState

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Structured output schema
# ---------------------------------------------------------------------------


class ArticleDraft(BaseModel):
    """
    The LLM fills this out — each field maps to Article sections.
    Keeping them separate forces the model to think in sections.
    """

    title: str = Field(
        description="Compelling but factual headline (max 100 chars)"
    )
    lead: str = Field(
        description=(
            "Opening paragraph (2-3 sentences). Must answer: Who, What, When, Where. "
            "This is the hook — readers decide here whether to continue."
        )
    )
    body: str = Field(
        description=(
            "Main article body (4-6 paragraphs). "
            "Expand on the facts, add context, include key details and quotes if available. "
            "Each paragraph should cover one distinct aspect."
        )
    )
    conclusion: str = Field(
        description=(
            "Closing paragraph (2-3 sentences). "
            "Summarize the implications or what to watch next. No new facts here."
        )
    )
    sources_used: list[str] = Field(
        description="URLs of sources used in the article"
    )


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


class JournalistAgent(BaseAgent):
    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self._lang = settings.newsroom_language
        self._lang_name = settings.language_name()
        self._min_words = settings.newsroom_min_words
        self._max_words = settings.newsroom_max_words

    def run(self, state: NewsroomState) -> dict:
        return self._timed_run("journalist", self._execute, state)

    def _execute(self, state: NewsroomState) -> dict:
        errors = list(state.get("errors", []))
        topic = state.get("topic")

        if topic is None:
            errors.append("Journalist: no topic in state")
            return {"status": ArticleStatus.FAILED.value, "errors": errors}

        # Build context from search results — wrapped to prevent injection
        source_context = self._build_source_context(topic)

        system = self._system_prompt()
        user = self._user_prompt(topic, source_context)

        try:
            llm = self.structured_llm(ArticleDraft)
            draft_raw: ArticleDraft = llm.invoke(
                [SystemMessage(content=system), HumanMessage(content=user)]
            )
        except Exception as e:
            log.error("journalist.llm_error", error=str(e))
            errors.append(f"Journalist LLM error: {e}")
            return {"status": ArticleStatus.FAILED.value, "errors": errors}

        # Validate and construct Article
        try:
            article = Article(
                topic_id=topic.id,
                title=draft_raw.title,
                lead=draft_raw.lead,
                body=draft_raw.body,
                conclusion=draft_raw.conclusion,
                sources=draft_raw.sources_used,
            )
        except Exception as e:
            log.error("journalist.validation_error", error=str(e))
            errors.append(f"Article validation failed: {e}")
            return {"status": ArticleStatus.FAILED.value, "errors": errors}

        log.info(
            "journalist.article_written",
            title=article.title,
            word_count=article.word_count,
        )

        return {
            "draft": article,
            "status": ArticleStatus.EDITING.value,
            "errors": errors,
        }

    # ------------------------------------------------------------------
    # Prompt builders
    # ------------------------------------------------------------------

    def _system_prompt(self) -> str:
        return get_prompt(
            "journalist.write",
            language=self._lang_name,
            min_words=self._min_words,
            max_words=self._max_words,
        )

    def _user_prompt(self, topic, source_context: str) -> str:
        return (
            f"Write a news article about the following topic:\n\n"
            f"**Topic:** {topic.title}\n"
            f"**Category:** {topic.category}\n"
            f"**Summary:** {topic.summary}\n\n"
            f"Use the following sources for facts and context:\n\n"
            f"{source_context}\n\n"
            "Write the article now."
        )

    def _build_source_context(self, topic) -> str:
        if not topic.sources:
            return "(No search results available — use general knowledge about this topic carefully.)"

        parts = []
        for i, result in enumerate(topic.sources[:5], 1):  # Cap at 5 sources
            safe_content = self.wrap_external_content(result.content)
            parts.append(
                f"--- Source {i} ---\n"
                f"Title: {result.title}\n"
                f"URL: {result.url}\n"
                f"Content:\n{safe_content}"
            )
        return "\n\n".join(parts)
