"""
Fact Checker Agent

Responsibilities:
1. Extract key factual claims from the edited article
2. Search each claim independently
3. Evaluate confidence for each claim against search results
4. Return an overall score and list of issues

The score drives the routing decision in the graph:
  score >= MIN_FACT_SCORE  → publish
  score <  MIN_FACT_SCORE AND revisions left → back to editor
  score <  MIN_FACT_SCORE AND no revisions left → reject
"""

from __future__ import annotations

import structlog
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from agents.base import BaseAgent
from config.prompts import get_prompt
from config.settings import Settings
from models.schemas import (
    Article,
    ArticleStatus,
    Claim,
    FactCheckResult,
    NewsroomState,
)
from tools.search import SearchTool

log = structlog.get_logger(__name__)

# Max claims to verify — more = more accurate but more API calls + cost
MAX_CLAIMS_TO_VERIFY = 5


# ---------------------------------------------------------------------------
# Structured output schemas
# ---------------------------------------------------------------------------


class ExtractedClaims(BaseModel):
    claims: list[str] = Field(
        description=(
            f"List of up to {MAX_CLAIMS_TO_VERIFY} specific, verifiable factual claims "
            "from the article. Each claim should be a standalone sentence "
            "that can be searched on the web (names, dates, numbers, events)."
        )
    )


class ClaimVerification(BaseModel):
    claim: str
    verdict: str = Field(
        description="'confirmed', 'partially_confirmed', 'unverified', or 'contradicted'"
    )
    confidence: float = Field(
        ge=0.0, le=1.0,
        description="Confidence score: 1.0 = fully confirmed, 0.0 = contradicted or no info"
    )
    notes: str = Field(
        description="Brief explanation of the verdict and which sources were used"
    )
    supporting_sources: list[str] = Field(
        default_factory=list,
        description="URLs that support or contradict this claim"
    )


class SearchQuery(BaseModel):
    """A concise web search query derived from a claim."""

    query: str = Field(
        min_length=3,
        max_length=120,
        description="Short keyword-based search query (3-8 keywords, English preferred for tech topics)",
    )


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


class FactCheckerAgent(BaseAgent):
    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self._search = SearchTool(
            tavily_api_key=settings.tavily_api_key.get_secret_value() if settings.has_tavily() else None,
            max_results=3,  # Fewer results per claim keeps cost low
            timeout=settings.search_timeout_seconds,
        )
        self._min_score = settings.newsroom_min_fact_score
        self._lang_name = settings.language_name()

    def run(self, state: NewsroomState) -> dict:
        return self._timed_run("fact_checker", self._execute, state)

    def _execute(self, state: NewsroomState) -> dict:
        errors = list(state.get("errors", []))
        article = state.get("edited_article")

        if article is None:
            errors.append("FactChecker: no edited article in state")
            return {"status": ArticleStatus.FAILED.value, "errors": errors}

        # Step 1: Extract claims
        claims_raw = self._extract_claims(article)
        if not claims_raw:
            log.warning("fact_checker.no_claims_extracted")
            # Give benefit of the doubt — can't verify nothing
            result = FactCheckResult(
                article_id=article.id,
                claims=[],
                overall_score=0.75,
                issues=["Could not extract verifiable claims from article"],
            )
            return self._routing_result(result, state, errors)

        # Step 2: Verify each claim
        verified_claims: list[Claim] = []
        issues: list[str] = []

        for claim_text in claims_raw[:MAX_CLAIMS_TO_VERIFY]:
            verification = self._verify_claim(claim_text, article)
            if verification is None:
                continue

            claim = Claim(
                text=claim_text,
                verified=verification.verdict in ("confirmed", "partially_confirmed"),
                confidence=verification.confidence,
                sources=verification.supporting_sources,
                notes=verification.notes,
            )
            verified_claims.append(claim)

            if verification.verdict == "contradicted":
                issues.append(f"CONTRADICTED: {claim_text[:100]} — {verification.notes}")
            elif verification.verdict == "unverified":
                issues.append(f"UNVERIFIED: {claim_text[:100]}")

            log.info(
                "fact_checker.claim_verified",
                verdict=verification.verdict,
                confidence=verification.confidence,
                claim=claim_text[:60],
            )

        result = FactCheckResult(
            article_id=article.id,
            claims=verified_claims,
            issues=issues,
        )
        # overall_score is computed by model_validator in FactCheckResult

        log.info(
            "fact_checker.done",
            score=result.overall_score,
            claims_checked=len(verified_claims),
            issues=len(issues),
        )

        return self._routing_result(result, state, errors)

    # ------------------------------------------------------------------
    # Routing helper
    # ------------------------------------------------------------------

    def _routing_result(
        self, result: FactCheckResult, state: NewsroomState, errors: list[str]
    ) -> dict:
        """
        Determines the next status based on fact-check score.
        The actual graph routing is done by `should_revise()` in graph.py,
        but we set status here to reflect what happened.
        """
        revision_count = state.get("revision_count", 0)
        max_revisions = self._settings.newsroom_max_revisions

        if result.overall_score >= self._min_score:
            next_status = ArticleStatus.PUBLISHING.value
        elif revision_count < max_revisions:
            next_status = ArticleStatus.EDITING.value
        else:
            next_status = ArticleStatus.REJECTED.value
            errors.append(
                f"Article rejected after {revision_count} revisions "
                f"(fact score: {result.overall_score:.2f}, "
                f"minimum required: {self._min_score:.2f})"
            )

        return {
            "fact_check": result,
            "status": next_status,
            "errors": errors,
        }

    # ------------------------------------------------------------------
    # LLM helpers
    # ------------------------------------------------------------------

    def _extract_claims(self, article: Article) -> list[str]:
        system = get_prompt(
            "fact_checker.extract",
            language=self._lang_name,
            max_claims=MAX_CLAIMS_TO_VERIFY,
        )
        user = (
            f"Article title: {article.title}\n\n"
            f"{article.full_text()}\n\n"
            "List the key verifiable claims."
        )

        try:
            llm = self.structured_llm(ExtractedClaims)
            result: ExtractedClaims = llm.invoke(
                [SystemMessage(content=system), HumanMessage(content=user)]
            )
            return result.claims
        except Exception as e:
            log.error("fact_checker.extract_error", error=str(e))
            return []

    def _verify_claim(self, claim: str, article: Article) -> ClaimVerification | None:
        # Build a short, keyword-based search query from the claim
        search_query = self._build_search_query(claim)
        search_results = self._search.search(search_query, topic="news")

        # If no results with the generated query, try a simplified English fallback
        if not search_results:
            fallback_query = self._build_english_query(claim)
            if fallback_query and fallback_query != search_query:
                log.info("fact_checker.search_retry", query=fallback_query[:80])
                search_results = self._search.search(fallback_query, topic="general")

        # Build context from search results (may be empty)
        context_parts = []
        for r in search_results:
            safe = self.wrap_external_content(r.content)
            context_parts.append(f"Source: {r.url}\n{safe}")
        context = "\n\n".join(context_parts) if context_parts else "(no search results found)"

        system = get_prompt("fact_checker.verify")
        user = (
            f"Claim to verify: \"{claim}\"\n\n"
            f"Search results:\n{context}\n\n"
            "Evaluate whether this claim is confirmed, partially_confirmed, "
            "unverified, or contradicted by the search results. "
            "If no search results were found, use your own knowledge to assess "
            "the claim — assign 'confirmed' with moderate confidence (0.6-0.7) "
            "for widely known facts, or 'unverified' with 0.5 for obscure claims."
        )

        try:
            llm = self.structured_llm(ClaimVerification)
            return llm.invoke([SystemMessage(content=system), HumanMessage(content=user)])
        except Exception as e:
            log.error("fact_checker.verify_error", claim=claim[:50], error=str(e))
            return None

    def _build_search_query(self, claim: str) -> str:
        """Convert a full-sentence claim to a concise search query."""
        try:
            llm = self.structured_llm(SearchQuery)
            result = llm.invoke([
                SystemMessage(content=(
                    "Convert the following factual claim into a short, effective "
                    "web search query (3-8 keywords). Use English if the claim is "
                    "about an international topic, technology, or widely known subject. "
                    "Remove filler words. Focus on names, products, and key facts."
                )),
                HumanMessage(content=claim),
            ])
            return result.query
        except Exception as e:
            log.warning("fact_checker.query_build_error", error=str(e))
            # Fallback: use first 60 chars of the claim
            return claim[:60]

    def _build_english_query(self, claim: str) -> str | None:
        """Build a simple English keyword query as a last-resort fallback."""
        try:
            llm = self.structured_llm(SearchQuery)
            result = llm.invoke([
                SystemMessage(content=(
                    "Translate the following claim into a short English web search "
                    "query (3-6 keywords). Only output the query, nothing else."
                )),
                HumanMessage(content=claim),
            ])
            return result.query
        except Exception:
            return None
