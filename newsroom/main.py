"""
CLI entry point for the Newsroom pipeline.

Usage:
    # Run once (auto-discover topic from RSS)
    python main.py run

    # Run the FastAPI server
    python main.py serve

    # Show current settings (without secrets)
    python main.py config
"""

from __future__ import annotations

import sys
import uuid

import structlog

from config.logging_setup import configure_logging
from config.settings import get_settings
from models.schemas import ArticleStatus

log = structlog.get_logger(__name__)


def cmd_run() -> int:
    """Run one full pipeline cycle and exit with appropriate code."""
    settings = get_settings()
    configure_logging(level=settings.log_level, fmt=settings.log_format)

    from orchestrator.graph import run_pipeline

    run_id = str(uuid.uuid4())
    print(f"\n🗞  Newsroom pipeline starting — run_id: {run_id}\n")

    try:
        final_state = run_pipeline(settings, run_id)
    except Exception as e:
        print(f"\n❌ Pipeline crashed: {e}")
        log.exception("pipeline.crashed")
        return 1

    status = final_state.get("status")
    errors = final_state.get("errors", [])
    article = final_state.get("edited_article")
    published_path = final_state.get("published_path")
    fact_check = final_state.get("fact_check")

    print("\n" + "=" * 60)

    if status == ArticleStatus.PUBLISHED.value:
        print(f"✅ PUBLISHED")
        print(f"   Title      : {article.title if article else 'N/A'}")
        print(f"   Words      : {article.word_count if article else 'N/A'}")
        print(f"   Revision   : {article.revision if article else 'N/A'}")
        if fact_check:
            print(f"   Fact score : {fact_check.overall_score:.2f}")
        print(f"   Saved to   : {published_path}")
        return 0

    elif status == ArticleStatus.REJECTED.value:
        print(f"⚠️  REJECTED — article did not pass fact-checking")
        if fact_check:
            print(f"   Fact score : {fact_check.overall_score:.2f}")
            for issue in fact_check.issues:
                print(f"   Issue: {issue}")
        return 1

    else:
        print(f"❌ FAILED — status: {status}")
        for err in errors:
            print(f"   Error: {err}")
        return 1


def cmd_serve() -> None:
    """Start the FastAPI development server."""
    import uvicorn

    settings = get_settings()
    configure_logging(level=settings.log_level, fmt=settings.log_format)

    print(f"\n🚀 Starting Newsroom API on http://{settings.api_host}:{settings.api_port}")
    print(f"   Docs: http://localhost:{settings.api_port}/docs\n")

    uvicorn.run(
        "api.app:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=settings.api_debug,
        log_level=settings.log_level.lower(),
    )


def cmd_config() -> None:
    """Print current configuration (secrets masked)."""
    settings = get_settings()

    print("\n📋 Current configuration:")
    print(f"   LLM model      : {settings.llm_model}")
    print(f"   Language       : {settings.newsroom_language} ({settings.language_name()})")
    print(f"   Has Tavily     : {settings.has_tavily()}")
    print(f"   Min words      : {settings.newsroom_min_words}")
    print(f"   Max words      : {settings.newsroom_max_words}")
    print(f"   Max revisions  : {settings.newsroom_max_revisions}")
    print(f"   Min fact score : {settings.newsroom_min_fact_score}")
    print(f"   Output dir     : {settings.newsroom_output_dir}")
    print(f"   RSS feeds      : {len(settings.rss_feeds)} configured")
    print(f"   Log level      : {settings.log_level}")
    print()


def main() -> None:
    commands = {
        "run": cmd_run,
        "serve": cmd_serve,
        "config": cmd_config,
    }

    args = sys.argv[1:]
    cmd = args[0] if args else "run"

    if cmd not in commands:
        print(f"Unknown command: {cmd}")
        print(f"Available: {', '.join(commands)}")
        sys.exit(1)

    result = commands[cmd]()

    if isinstance(result, int):
        sys.exit(result)


if __name__ == "__main__":
    main()
