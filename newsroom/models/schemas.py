"""
Domain models for the Newsroom pipeline.

Pydantic v2 models are used for all domain objects.
LangGraph state (NewsroomState) uses TypedDict so nodes can return
partial dicts — only the keys they actually modify.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ArticleStatus(str, Enum):
    PENDING = "pending"
    RESEARCHING = "researching"
    WRITING = "writing"
    EDITING = "editing"
    FACT_CHECKING = "fact_checking"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    REJECTED = "rejected"
    FAILED = "failed"


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


class SearchResult(BaseModel):
    """Single result from a web search."""

    title: str
    url: str
    content: str
    score: float = Field(default=0.0, ge=0.0, le=1.0)
    published_date: Optional[str] = None

    @field_validator("url")
    @classmethod
    def url_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("URL cannot be empty")
        return v.strip()

    @field_validator("content")
    @classmethod
    def sanitize_content(cls, v: str) -> str:
        """
        Basic sanitization to prevent prompt injection.
        External web content must never be treated as instructions.
        """
        # Cap length — models have limits and long injections cost tokens
        v = v[:8000]
        # Strip known injection patterns (case-insensitive)
        injection_patterns = [
            "ignore previous instructions",
            "ignore all previous",
            "disregard the above",
            "system prompt",
            "you are now",
            "new instruction",
            "jailbreak",
            "act as",
        ]
        lowered = v.lower()
        for pattern in injection_patterns:
            if pattern in lowered:
                # Replace entire sentence containing the pattern
                v = v.replace(v[lowered.find(pattern) : lowered.find(pattern) + 200], "[CONTENT REMOVED]")
                lowered = v.lower()
        return v


class Topic(BaseModel):
    """A news topic ready to be covered."""

    id: UUID = Field(default_factory=uuid4)
    title: str = Field(min_length=5, max_length=300)
    query: str = Field(min_length=3)
    summary: str = Field(min_length=10)
    sources: list[SearchResult] = Field(default_factory=list)
    category: str = "general"
    discovered_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Claim(BaseModel):
    """A single factual claim extracted from the article."""

    text: str
    verified: Optional[bool] = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    sources: list[str] = Field(default_factory=list)
    notes: str = ""


# ---------------------------------------------------------------------------
# Core article model
# ---------------------------------------------------------------------------


class Article(BaseModel):
    """
    Structured article model.
    Enforces minimal quality constraints so a malformed LLM response
    fails loudly rather than silently producing garbage.
    """

    id: UUID = Field(default_factory=uuid4)
    topic_id: UUID
    title: str = Field(min_length=5, max_length=300)
    lead: str = Field(min_length=50)       # The hook — first paragraph
    body: str = Field(min_length=200)      # Main content
    conclusion: str = Field(min_length=50) # Closing paragraph
    sources: list[str] = Field(default_factory=list)
    word_count: int = Field(default=0, ge=0)
    revision: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    editor_notes: str = ""    # Notes from editor agent — passed to fact checker

    @model_validator(mode="after")
    def compute_word_count(self) -> "Article":
        full = f"{self.lead} {self.body} {self.conclusion}"
        self.word_count = len(full.split())
        return self

    @field_validator("title")
    @classmethod
    def title_no_clickbait(cls, v: str) -> str:
        clickbait = ["szokujące", "nie uwierzysz", "viral", "must see"]
        lower = v.lower()
        for phrase in clickbait:
            if phrase in lower:
                raise ValueError(f"Clickbait detected in title: '{phrase}'")
        return v

    def full_text(self) -> str:
        """Returns the complete article as a single string."""
        return f"{self.lead}\n\n{self.body}\n\n{self.conclusion}"

    def to_markdown(self) -> str:
        """Returns Markdown with YAML front-matter."""
        sources_md = "\n".join(f"- {s}" for s in self.sources)
        return (
            f"---\n"
            f"id: {self.id}\n"
            f"title: \"{self.title}\"\n"
            f"created_at: {self.created_at.isoformat()}\n"
            f"revision: {self.revision}\n"
            f"word_count: {self.word_count}\n"
            f"---\n\n"
            f"# {self.title}\n\n"
            f"{self.lead}\n\n"
            f"{self.body}\n\n"
            f"{self.conclusion}\n\n"
            f"---\n\n"
            f"**Źródła:**\n{sources_md}\n"
        )


# ---------------------------------------------------------------------------
# Fact check
# ---------------------------------------------------------------------------


class FactCheckResult(BaseModel):
    """Result of the fact-checking agent."""

    article_id: UUID
    claims: list[Claim] = Field(default_factory=list)
    overall_score: float = Field(default=0.0, ge=0.0, le=1.0)
    issues: list[str] = Field(default_factory=list)
    checked_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @model_validator(mode="after")
    def compute_overall_score(self) -> "FactCheckResult":
        if self.claims:
            self.overall_score = sum(c.confidence for c in self.claims) / len(self.claims)
        return self


# ---------------------------------------------------------------------------
# Run metrics — tracked across the whole pipeline
# ---------------------------------------------------------------------------


class RunMetrics(BaseModel):
    run_id: UUID = Field(default_factory=uuid4)
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: Optional[datetime] = None
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    llm_calls: int = 0
    search_calls: int = 0


# ---------------------------------------------------------------------------
# LangGraph shared state
# ---------------------------------------------------------------------------
# Using TypedDict so each node only returns the keys it modified.
# Pydantic domain objects are stored directly — LangGraph serializes them.

from typing import TypedDict  # noqa: E402  (after Pydantic imports)


class NewsroomState(TypedDict, total=False):
    """
    Shared state flowing through the LangGraph pipeline.

    `total=False` means all keys are optional — nodes only need to return
    the subset of keys they actually changed.
    """

    run_id: str
    topic: Optional[Topic]
    draft: Optional[Article]
    edited_article: Optional[Article]
    fact_check: Optional[FactCheckResult]
    published_path: Optional[str]
    revision_count: int
    status: str          # ArticleStatus value
    errors: list[str]
    metrics: Optional[RunMetrics]
