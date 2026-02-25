"""
Unit tests for each agent.

All LLM calls are mocked so tests run fast and offline.
We test:
- Happy path: agent produces correct output
- Missing state: agent returns FAILED cleanly
- LLM errors: agent handles exceptions gracefully
- Validation: structured output is validated correctly
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from agents.editor import EditorAgent, EditedArticle
from agents.fact_checker import FactCheckerAgent, ClaimVerification, ExtractedClaims
from agents.journalist import JournalistAgent, ArticleDraft
from agents.publisher import PublisherAgent
from agents.topic_scout import TopicScoutAgent, TopicSelection
from models.schemas import Article, ArticleStatus, FactCheckResult, SearchResult


# ---------------------------------------------------------------------------
# Topic Scout
# ---------------------------------------------------------------------------


class TestTopicScoutAgent:
    def test_returns_topic_on_success(self, test_settings, initial_state):
        from tools.rss import RssEntry
        from datetime import datetime, timezone

        scout = TopicScoutAgent(test_settings)

        mock_entry = RssEntry(
            title="AI Breakthrough Announced",
            url="https://example.com/ai",
            summary="Summary",
            published_at=datetime.now(timezone.utc),
            source_name="Test Source",
        )
        mock_selection = TopicSelection(
            selected_title="AI Breakthrough Announced",
            reasoning="Very newsworthy",
            search_queries=["AI breakthrough 2026", "AI research scientists", "AI technology news"],
            category="technology",
            summary="Scientists announce a major AI breakthrough.",
        )
        mock_search_results = []

        with patch.object(scout._rss, "fetch_recent", return_value=[mock_entry]):
            with patch.object(scout, "_select_topic", return_value=mock_selection):
                with patch.object(scout._search, "search_multiple", return_value=mock_search_results):
                    result = scout.run(initial_state)

        assert result["topic"] is not None
        assert result["topic"].title == "AI Breakthrough Announced"
        assert result["status"] == ArticleStatus.WRITING.value

    def test_no_rss_entries_returns_failed(self, test_settings, initial_state):
        scout = TopicScoutAgent(test_settings)

        with patch.object(scout._rss, "fetch_recent", return_value=[]):
            result = scout.run(initial_state)

        assert result["status"] == ArticleStatus.FAILED.value
        assert len(result["errors"]) > 0

    def test_llm_error_returns_failed(self, test_settings, initial_state):
        from tools.rss import RssEntry
        from datetime import datetime, timezone

        scout = TopicScoutAgent(test_settings)
        mock_entry = RssEntry(
            title="Test", url="https://x.com", summary="S",
            published_at=datetime.now(timezone.utc), source_name="X"
        )

        with patch.object(scout._rss, "fetch_recent", return_value=[mock_entry]):
            with patch.object(scout, "_select_topic", return_value=None):
                result = scout.run(initial_state)

        assert result["status"] == ArticleStatus.FAILED.value


# ---------------------------------------------------------------------------
# Journalist
# ---------------------------------------------------------------------------


class TestJournalistAgent:
    def test_writes_article_from_topic(self, test_settings, state_with_topic):
        journalist = JournalistAgent(test_settings)

        mock_draft = ArticleDraft(
            title="Naukowcy ogłaszają przełom w AI",
            lead="Zespół badaczy ogłosił przełomowe odkrycie w dziedzinie AI.",
            body=(
                "Nowy model osiągnął wyniki przewyższające ludzką wydajność. "
                "Badacze twierdzą, że kluczem był nowy algorytm.\n\n"
                "Wyniki zostaną opublikowane w Nature. Eksperci oceniają odkrycie "
                "jako transformacyjne dla całej branży. Konferencja odbędzie się w grudniu."
            ),
            conclusion="Odkrycie może zmienić medycynę i edukację w skali globalnej.",
            sources_used=["https://example.com/ai-article"],
        )

        mock_llm = MagicMock()
        mock_llm.invoke.return_value = mock_draft

        with patch.object(journalist, "structured_llm", return_value=mock_llm):
            result = journalist.run(state_with_topic)

        assert result["draft"] is not None
        assert result["draft"].title == "Naukowcy ogłaszają przełom w AI"
        assert result["status"] == ArticleStatus.EDITING.value

    def test_no_topic_returns_failed(self, test_settings, initial_state):
        journalist = JournalistAgent(test_settings)
        result = journalist.run(initial_state)

        assert result["status"] == ArticleStatus.FAILED.value
        assert "no topic" in result["errors"][0].lower()

    def test_llm_exception_returns_failed(self, test_settings, state_with_topic):
        journalist = JournalistAgent(test_settings)

        mock_llm = MagicMock()
        mock_llm.invoke.side_effect = Exception("API timeout")

        with patch.object(journalist, "structured_llm", return_value=mock_llm):
            result = journalist.run(state_with_topic)

        assert result["status"] == ArticleStatus.FAILED.value
        assert "API timeout" in result["errors"][0]


# ---------------------------------------------------------------------------
# Editor
# ---------------------------------------------------------------------------


class TestEditorAgent:
    def test_edits_draft_article(self, test_settings, state_with_draft, sample_article):
        editor = EditorAgent(test_settings)

        mock_edited = EditedArticle(
            title="Przełomowe odkrycie w dziedzinie sztucznej inteligencji",
            lead="Badacze ogłosili przełom w AI, który może zmienić technologię.",
            body=(
                "Nowy model osiągnął wyniki przewyższające człowieka we wszystkich testach "
                "rozumienia języka naturalnego. Kluczem do sukcesu był nowatorski algorytm "
                "uczenia maszynowego opracowany przez zespół badaczy. "
                "Wyniki zostaną opublikowane w prestiżowym czasopiśmie Nature w przyszłym "
                "tygodniu. Konferencja naukowa odbędzie się w grudniu w Warszawie. "
                "Eksperci z całego świata uważają to odkrycie za transformacyjne dla branży."
            ),
            conclusion="Odkrycie otwiera nowe możliwości w medycynie i edukacji.",
            sources=["https://example.com/ai-article"],
            editor_notes="Verify: 'results surpassing human performance'",
            changes_summary="Improved lead clarity, tightened body",
        )

        mock_llm = MagicMock()
        mock_llm.invoke.return_value = mock_edited

        with patch.object(editor, "structured_llm", return_value=mock_llm):
            result = editor.run(state_with_draft)

        assert result["edited_article"] is not None
        assert result["edited_article"].revision == 1
        assert result["status"] == ArticleStatus.FACT_CHECKING.value

    def test_no_article_returns_failed(self, test_settings, initial_state):
        editor = EditorAgent(test_settings)
        result = editor.run(initial_state)

        assert result["status"] == ArticleStatus.FAILED.value

    def test_revision_increments_counter(self, test_settings, state_with_edited, sample_fact_check):
        """When called on a revision, article.revision should be incremented."""
        state = {
            **state_with_edited,
            "fact_check": sample_fact_check,
            "revision_count": 1,
        }
        editor = EditorAgent(test_settings)

        mock_edited = EditedArticle(
            title="Title",
            lead="Lead paragraph with enough content to pass validation.",
            body="Body " * 50,
            conclusion="Conclusion paragraph that wraps things up nicely for the reader.",
            sources=[],
            editor_notes="Notes",
            changes_summary="Fixed issues",
        )
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = mock_edited

        with patch.object(editor, "structured_llm", return_value=mock_llm):
            result = editor.run(state)

        assert result["edited_article"].revision == 2


# ---------------------------------------------------------------------------
# Fact Checker
# ---------------------------------------------------------------------------


class TestFactCheckerAgent:
    def test_high_score_sets_publishing_status(
        self, test_settings, state_with_edited
    ):
        checker = FactCheckerAgent(test_settings)

        mock_claims = ExtractedClaims(
            claims=["Model surpassed human performance in language tests"]
        )
        mock_verification = ClaimVerification(
            claim="Model surpassed human performance in language tests",
            verdict="confirmed",
            confidence=0.90,
            notes="Multiple sources confirm",
            supporting_sources=["https://example.com"],
        )

        mock_extract_llm = MagicMock()
        mock_extract_llm.invoke.return_value = mock_claims

        mock_verify_llm = MagicMock()
        mock_verify_llm.invoke.return_value = mock_verification

        mock_search_result = SearchResult(
            title="AI test", url="https://example.com/ai", content="AI surpassed humans.", score=0.9
        )
        with patch.object(checker._search, "search", return_value=[mock_search_result]):
            with patch.object(
                checker, "structured_llm",
                side_effect=[mock_extract_llm, mock_verify_llm],
            ):
                result = checker.run(state_with_edited)

        assert result["fact_check"] is not None
        assert result["status"] == ArticleStatus.PUBLISHING.value

    def test_low_score_sets_editing_status_when_revisions_remain(
        self, test_settings, state_with_edited
    ):
        checker = FactCheckerAgent(test_settings)

        mock_claims = ExtractedClaims(claims=["Unverifiable claim"])
        mock_verification = ClaimVerification(
            claim="Unverifiable claim",
            verdict="contradicted",
            confidence=0.10,
            notes="Contradicted by sources",
        )

        mock_extract_llm = MagicMock()
        mock_extract_llm.invoke.return_value = mock_claims
        mock_verify_llm = MagicMock()
        mock_verify_llm.invoke.return_value = mock_verification

        with patch.object(checker._search, "search", return_value=[]):
            with patch.object(
                checker, "structured_llm",
                side_effect=[mock_extract_llm, mock_verify_llm],
            ):
                result = checker.run(state_with_edited)

        assert result["status"] == ArticleStatus.EDITING.value

    def test_low_score_rejects_when_no_revisions_left(
        self, test_settings, state_with_edited
    ):
        state = {**state_with_edited, "revision_count": 99}
        checker = FactCheckerAgent(test_settings)

        mock_claims = ExtractedClaims(claims=["Bad claim"])
        mock_verification = ClaimVerification(
            claim="Bad claim",
            verdict="contradicted",
            confidence=0.05,
            notes="Contradicted",
        )

        mock_extract_llm = MagicMock()
        mock_extract_llm.invoke.return_value = mock_claims
        mock_verify_llm = MagicMock()
        mock_verify_llm.invoke.return_value = mock_verification

        with patch.object(checker._search, "search", return_value=[]):
            with patch.object(
                checker, "structured_llm",
                side_effect=[mock_extract_llm, mock_verify_llm],
            ):
                result = checker.run(state)

        assert result["status"] == ArticleStatus.REJECTED.value
        assert len(result["errors"]) > 0

    def test_no_article_returns_failed(self, test_settings, initial_state):
        checker = FactCheckerAgent(test_settings)
        result = checker.run(initial_state)
        assert result["status"] == ArticleStatus.FAILED.value


# ---------------------------------------------------------------------------
# Publisher
# ---------------------------------------------------------------------------


class TestPublisherAgent:
    def test_saves_markdown_file(self, test_settings, state_ready_to_publish, tmp_path):
        test_settings_patched = test_settings.model_copy(
            update={"newsroom_output_dir": str(tmp_path)}
        )
        publisher = PublisherAgent(test_settings_patched)
        result = publisher.run(state_ready_to_publish)

        assert result["status"] == ArticleStatus.PUBLISHED.value
        assert result["published_path"] is not None

        saved_path = result["published_path"]
        assert saved_path.endswith(".md")
        content = open(saved_path, encoding="utf-8").read()
        assert "# " in content  # Has a heading

    def test_manifest_updated_after_publish(self, test_settings, state_ready_to_publish, tmp_path):
        import json

        test_settings_patched = test_settings.model_copy(
            update={"newsroom_output_dir": str(tmp_path)}
        )
        publisher = PublisherAgent(test_settings_patched)
        publisher.run(state_ready_to_publish)

        manifest_path = tmp_path / "manifest.json"
        assert manifest_path.exists()

        manifest = json.loads(manifest_path.read_text())
        assert len(manifest["articles"]) == 1
        assert "title" in manifest["articles"][0]

    def test_no_article_returns_failed(self, test_settings, initial_state):
        publisher = PublisherAgent(test_settings)
        result = publisher.run(initial_state)
        assert result["status"] == ArticleStatus.FAILED.value

    def test_slugify_polish_characters(self):
        from agents.publisher import PublisherAgent as P

        slug = P._slugify("Wielki pożar w Krakowie!")
        assert slug == "wielki-pozar-w-krakowie"
        assert "!" not in slug
