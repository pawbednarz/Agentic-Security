"""
RSS feed reader for the Topic Scout agent.

Fetches and filters entries from configured RSS feeds.
Entries older than `max_age_hours` are discarded to keep topics fresh.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Optional

import structlog

log = structlog.get_logger(__name__)


class RssEntry:
    """Lightweight value object representing a single RSS item."""

    __slots__ = ("title", "url", "summary", "published_at", "source_name")

    def __init__(
        self,
        title: str,
        url: str,
        summary: str,
        published_at: Optional[datetime],
        source_name: str,
    ) -> None:
        self.title = title
        self.url = url
        self.summary = summary
        self.published_at = published_at
        self.source_name = source_name

    def __repr__(self) -> str:
        return f"RssEntry(title={self.title!r}, source={self.source_name!r})"


class RssReader:
    """
    Reads multiple RSS feeds and returns recent entries.

    Usage:
        reader = RssReader(feeds=[...], max_age_hours=24)
        entries = reader.fetch_recent()
    """

    def __init__(self, feeds: list[str], max_age_hours: int = 24) -> None:
        if not feeds:
            raise ValueError("At least one RSS feed URL must be provided")
        self._feeds = feeds
        self._max_age_hours = max_age_hours

    def fetch_recent(self) -> list[RssEntry]:
        """
        Fetches all configured feeds and returns entries newer than max_age_hours.
        Entries are sorted newest-first.
        """
        try:
            import feedparser  # type: ignore
        except ImportError as exc:
            raise ImportError("feedparser is required: pip install feedparser") from exc

        cutoff = datetime.now(timezone.utc).timestamp() - self._max_age_hours * 3600
        all_entries: list[RssEntry] = []

        for feed_url in self._feeds:
            log.info("rss.fetching", url=feed_url)
            try:
                feed = feedparser.parse(feed_url)

                if feed.bozo and not feed.entries:
                    log.warning("rss.parse_error", url=feed_url, reason=str(feed.bozo_exception))
                    continue

                source_name = feed.feed.get("title", feed_url)

                for entry in feed.entries:
                    published_at = self._parse_date(entry)
                    if published_at and published_at.timestamp() < cutoff:
                        continue  # Too old

                    title = entry.get("title", "").strip()
                    url = entry.get("link", "").strip()
                    summary = entry.get("summary", entry.get("description", "")).strip()

                    if not title or not url:
                        continue

                    all_entries.append(
                        RssEntry(
                            title=title[:300],
                            url=url,
                            summary=summary[:500],
                            published_at=published_at,
                            source_name=source_name,
                        )
                    )

                log.info("rss.fetched", url=feed_url, count=len(feed.entries))

            except Exception as e:
                log.error("rss.fetch_failed", url=feed_url, error=str(e))

        # Sort: newest first (None published_at goes to the end)
        all_entries.sort(
            key=lambda e: e.published_at.timestamp() if e.published_at else 0,
            reverse=True,
        )
        log.info("rss.total_entries", count=len(all_entries))
        return all_entries

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_date(entry) -> Optional[datetime]:
        """Try to extract a timezone-aware datetime from a feedparser entry."""
        for attr in ("published_parsed", "updated_parsed", "created_parsed"):
            t = getattr(entry, attr, None)
            if t is not None:
                try:
                    return datetime.fromtimestamp(time.mktime(t), tz=timezone.utc)
                except (OverflowError, ValueError):
                    pass
        return None
