"""
Shared test fixtures.

Key principle: no real API calls in tests.
All LLM and search calls are mocked via pytest-mock / monkeypatch.
"""

from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from config.settings import Settings, get_settings
from models.schemas import (
    Article,
    ArticleStatus,
    Claim,
    FactCheckResult,
    NewsroomState,
    SearchResult,
    Topic,
)


# ---------------------------------------------------------------------------
# Settings fixture — overrides real settings with test values
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clear_settings_cache():
    """Clear lru_cache between tests so monkeypatched env vars take effect."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def test_settings(monkeypatch) -> Settings:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test-key")
    monkeypatch.setenv("NEWSROOM_LANGUAGE", "pl")
    monkeypatch.setenv("NEWSROOM_OUTPUT_DIR", "/tmp/newsroom_test_output")
    monkeypatch.setenv("NEWSROOM_CHECKPOINTS_DIR", "/tmp/newsroom_test_checkpoints")
    monkeypatch.setenv("API_SECRET_KEY", "test-secret")
    get_settings.cache_clear()
    return get_settings()


# ---------------------------------------------------------------------------
# Domain object fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_search_result() -> SearchResult:
    return SearchResult(
        title="Test article about AI",
        url="https://example.com/ai-article",
        content="This is some content about artificial intelligence developments.",
        score=0.9,
    )


@pytest.fixture
def sample_topic(sample_search_result) -> Topic:
    return Topic(
        title="Przełom w dziedzinie sztucznej inteligencji",
        query="artificial intelligence breakthrough 2026",
        summary="Scientists announced a major breakthrough in AI research.",
        sources=[sample_search_result],
        category="technology",
    )


@pytest.fixture
def sample_article(sample_topic) -> Article:
    return Article(
        topic_id=sample_topic.id,
        title="Naukowcy ogłaszają przełom w AI",
        lead=(
            "Zespół badaczy z Instytutu Technologii ogłosił w piątek przełomowe "
            "odkrycie w dziedzinie sztucznej inteligencji, które może zrewolucjonizować "
            "sposób przetwarzania języka naturalnego przez maszyny."
        ),
        body=(
            "Nowy model językowy osiągnął wyniki przewyższające ludzką wydajność w "
            "testach rozumienia tekstu. Badacze twierdzą, że kluczem do sukcesu było "
            "zastosowanie innowacyjnej architektury transformerów.\n\n"
            "Profesor Jan Kowalski, kierownik projektu, powiedział: 'To największy "
            "postęp w naszej dziedzinie od dekady.'\n\n"
            "Wyniki badań zostaną opublikowane w prestiżowym czasopiśmie Nature "
            "w przyszłym tygodniu. Eksperci z branży oceniają odkrycie jako "
            "potencjalnie transformacyjne dla całej branży technologicznej."
        ),
        conclusion=(
            "Odkrycie może otworzyć nowe możliwości w medycynie, edukacji i przemyśle. "
            "Badacze planują przedstawić szczegóły techniczne na konferencji NeurIPS "
            "w grudniu bieżącego roku."
        ),
        sources=["https://example.com/ai-article"],
    )


@pytest.fixture
def sample_fact_check(sample_article) -> FactCheckResult:
    return FactCheckResult(
        article_id=sample_article.id,
        claims=[
            Claim(
                text="Nowy model językowy osiągnął wyniki przewyższające ludzką wydajność",
                verified=True,
                confidence=0.85,
                notes="Confirmed by multiple sources",
            ),
            Claim(
                text="Wyniki badań zostaną opublikowane w Nature",
                verified=True,
                confidence=0.80,
                notes="Confirmed by press release",
            ),
        ],
        issues=[],
    )


@pytest.fixture
def initial_state() -> NewsroomState:
    return {
        "run_id": str(uuid4()),
        "topic": None,
        "draft": None,
        "edited_article": None,
        "fact_check": None,
        "published_path": None,
        "revision_count": 0,
        "status": ArticleStatus.PENDING.value,
        "errors": [],
        "metrics": None,
    }


@pytest.fixture
def state_with_topic(initial_state, sample_topic) -> NewsroomState:
    return {**initial_state, "topic": sample_topic, "status": ArticleStatus.WRITING.value}


@pytest.fixture
def state_with_draft(state_with_topic, sample_article) -> NewsroomState:
    return {**state_with_topic, "draft": sample_article, "status": ArticleStatus.EDITING.value}


@pytest.fixture
def state_with_edited(state_with_draft, sample_article) -> NewsroomState:
    edited = sample_article.model_copy(update={"revision": 1})
    return {**state_with_draft, "edited_article": edited, "status": ArticleStatus.FACT_CHECKING.value}


@pytest.fixture
def state_ready_to_publish(state_with_edited, sample_fact_check) -> NewsroomState:
    return {
        **state_with_edited,
        "fact_check": sample_fact_check,
        "status": ArticleStatus.PUBLISHING.value,
    }
