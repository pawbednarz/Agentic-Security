"""
Tests for the LangGraph orchestrator routing logic.

We test the routing functions directly (unit tests) without
running the full graph — no real agents, no API calls.
"""

from __future__ import annotations

import pytest

from models.schemas import ArticleStatus
from orchestrator.graph import increment_revision, route_after_fact_check, route_after_scout


# ---------------------------------------------------------------------------
# route_after_scout
# ---------------------------------------------------------------------------


class TestRouteAfterScout:
    def test_routes_to_journalist_when_topic_found(self, sample_topic, initial_state):
        state = {**initial_state, "topic": sample_topic, "status": ArticleStatus.WRITING.value}
        assert route_after_scout(state) == "journalist"

    def test_routes_to_end_when_no_topic(self, initial_state):
        from langgraph.graph import END
        state = {**initial_state, "topic": None}
        assert route_after_scout(state) == END

    def test_routes_to_end_when_failed(self, sample_topic, initial_state):
        from langgraph.graph import END
        state = {
            **initial_state,
            "topic": sample_topic,
            "status": ArticleStatus.FAILED.value,
        }
        assert route_after_scout(state) == END


# ---------------------------------------------------------------------------
# route_after_fact_check
# ---------------------------------------------------------------------------


class TestRouteAfterFactCheck:
    def test_routes_to_publisher_when_publishing(self, initial_state):
        state = {**initial_state, "status": ArticleStatus.PUBLISHING.value}
        assert route_after_fact_check(state) == "publisher"

    def test_routes_to_editor_when_editing(self, initial_state):
        state = {**initial_state, "status": ArticleStatus.EDITING.value, "revision_count": 0}
        assert route_after_fact_check(state) == "editor"

    def test_routes_to_end_when_rejected(self, initial_state):
        from langgraph.graph import END
        state = {**initial_state, "status": ArticleStatus.REJECTED.value}
        assert route_after_fact_check(state) == END

    def test_routes_to_end_when_failed(self, initial_state):
        from langgraph.graph import END
        state = {**initial_state, "status": ArticleStatus.FAILED.value}
        assert route_after_fact_check(state) == END


# ---------------------------------------------------------------------------
# increment_revision
# ---------------------------------------------------------------------------


class TestIncrementRevision:
    def test_increments_from_zero(self, initial_state):
        state = {**initial_state, "revision_count": 0}
        result = increment_revision(state)
        assert result["revision_count"] == 1

    def test_increments_from_existing(self, initial_state):
        state = {**initial_state, "revision_count": 1}
        result = increment_revision(state)
        assert result["revision_count"] == 2

    def test_handles_missing_revision_count(self, initial_state):
        state = {k: v for k, v in initial_state.items() if k != "revision_count"}
        result = increment_revision(state)
        assert result["revision_count"] == 1


# ---------------------------------------------------------------------------
# FactCheckResult score computation
# ---------------------------------------------------------------------------


class TestFactCheckScoreComputation:
    def test_score_is_average_of_claim_confidences(self):
        from models.schemas import Claim, FactCheckResult
        from uuid import uuid4

        result = FactCheckResult(
            article_id=uuid4(),
            claims=[
                Claim(text="Claim A", confidence=0.8),
                Claim(text="Claim B", confidence=0.6),
            ],
        )
        assert abs(result.overall_score - 0.7) < 0.001

    def test_score_is_zero_with_no_claims(self):
        from models.schemas import FactCheckResult
        from uuid import uuid4

        result = FactCheckResult(article_id=uuid4(), claims=[])
        assert result.overall_score == 0.0


# ---------------------------------------------------------------------------
# Article model
# ---------------------------------------------------------------------------


class TestArticleModel:
    def test_word_count_computed_automatically(self, sample_topic):
        from models.schemas import Article

        article = Article(
            topic_id=sample_topic.id,
            title="Test",
            lead="One two three four five.",
            body="Six seven eight nine ten eleven.",
            conclusion="Twelve thirteen.",
        )
        # word_count is computed from lead + body + conclusion
        assert article.word_count > 0

    def test_markdown_contains_title(self, sample_article):
        md = sample_article.to_markdown()
        assert f"# {sample_article.title}" in md
        assert "---" in md  # YAML front-matter

    def test_clickbait_title_raises(self, sample_topic):
        from models.schemas import Article

        with pytest.raises(ValueError, match="Clickbait"):
            Article(
                topic_id=sample_topic.id,
                title="Szokujące odkrycie zmieni świat na zawsze",
                lead="Lead " * 15,
                body="Body " * 50,
                conclusion="Conclusion " * 10,
            )
