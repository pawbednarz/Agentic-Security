"""
Search tool with Tavily as primary provider and DuckDuckGo as fallback.

Design decisions:
- Tavily is preferred: it's optimized for AI agents and returns cleaner content.
- DuckDuckGo is the free fallback — no API key required.
- All results go through SearchResult validation which sanitizes content
  (prompt injection defense happens in the schema, not here).
- Rate limiting: a simple per-instance counter with sleep to avoid bans.
"""

from __future__ import annotations

import time
from typing import Optional

import structlog

from models.schemas import SearchResult

log = structlog.get_logger(__name__)


class SearchTool:
    """
    Wrapper around Tavily + DuckDuckGo with automatic fallback.

    Usage:
        tool = SearchTool(tavily_api_key="...", max_results=5)
        results = tool.search("latest news about AI")
    """

    # Conservative rate limit: 1 request / second for DDG to avoid blocks
    _DDG_INTERVAL_SECONDS = 1.2

    def __init__(
        self,
        tavily_api_key: Optional[str] = None,
        max_results: int = 5,
        timeout: int = 15,
    ) -> None:
        self._max_results = max_results
        self._timeout = timeout
        self._last_ddg_call: float = 0.0

        # Lazy-init so missing optional deps don't crash at import time
        self._tavily_client = None
        if tavily_api_key:
            try:
                from tavily import TavilyClient  # type: ignore

                self._tavily_client = TavilyClient(api_key=tavily_api_key)
                log.info("search.provider", provider="tavily")
            except ImportError:
                log.warning("search.tavily_not_installed", fallback="duckduckgo")
        else:
            log.info("search.provider", provider="duckduckgo_fallback")

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def search_multiple(
        self, queries: list[str], topic: str = "general"
    ) -> list[SearchResult]:
        """
        Run multiple search queries and return deduplicated results.

        Each query is searched independently. Results are merged,
        deduplicated by URL, and sorted by relevance score (highest first).
        This gives the journalist a broader, more diverse set of sources
        than a single query can provide.
        """
        if not queries:
            raise ValueError("At least one query is required")

        seen_urls: set[str] = set()
        all_results: list[SearchResult] = []

        for query in queries:
            try:
                results = self.search(query, topic=topic)
                for r in results:
                    if r.url not in seen_urls:
                        seen_urls.add(r.url)
                        all_results.append(r)
            except Exception as e:
                log.warning("search.multi_query_error", query=query[:60], error=str(e))

        all_results.sort(key=lambda r: r.score, reverse=True)
        log.info(
            "search.multi_done",
            queries=len(queries),
            unique_results=len(all_results),
        )
        return all_results

    def search(self, query: str, topic: str = "general") -> list[SearchResult]:
        """
        Search the web. Returns up to `max_results` sanitized results.

        Args:
            query: Natural language search query
            topic: Tavily topic hint ('general', 'news', 'finance', ...)
        """
        if not query.strip():
            raise ValueError("Search query cannot be empty")

        log.info("search.start", query=query[:80])

        if self._tavily_client:
            results = self._search_tavily(query, topic)
            if results:
                return results
            log.warning("search.tavily_empty", fallback="duckduckgo")

        return self._search_duckduckgo(query)

    # ------------------------------------------------------------------
    # Providers
    # ------------------------------------------------------------------

    def _search_tavily(self, query: str, topic: str) -> list[SearchResult]:
        try:
            response = self._tavily_client.search(
                query=query,
                search_depth="advanced",
                topic=topic,
                max_results=self._max_results,
                include_raw_content=False,
            )
            results = []
            for r in response.get("results", []):
                try:
                    results.append(
                        SearchResult(
                            title=r.get("title", ""),
                            url=r.get("url", ""),
                            content=r.get("content", ""),
                            score=float(r.get("score", 0.0)),
                            published_date=r.get("published_date"),
                        )
                    )
                except Exception as e:
                    log.warning("search.result_invalid", error=str(e))
            log.info("search.tavily_done", count=len(results))
            return results
        except Exception as e:
            log.error("search.tavily_error", error=str(e))
            return []

    def _search_duckduckgo(self, query: str) -> list[SearchResult]:
        # Respect DDG rate limit
        elapsed = time.monotonic() - self._last_ddg_call
        if elapsed < self._DDG_INTERVAL_SECONDS:
            time.sleep(self._DDG_INTERVAL_SECONDS - elapsed)

        try:
            from duckduckgo_search import DDGS  # type: ignore

            with DDGS(timeout=self._timeout) as ddgs:
                raw = list(ddgs.text(query, max_results=self._max_results))

            self._last_ddg_call = time.monotonic()

            results = []
            for r in raw:
                try:
                    results.append(
                        SearchResult(
                            title=r.get("title", ""),
                            url=r.get("href", ""),
                            content=r.get("body", ""),
                            score=0.5,  # DDG doesn't provide relevance scores
                        )
                    )
                except Exception as e:
                    log.warning("search.result_invalid", error=str(e))

            log.info("search.ddg_done", count=len(results))
            return results
        except Exception as e:
            log.error("search.ddg_error", error=str(e))
            return []
