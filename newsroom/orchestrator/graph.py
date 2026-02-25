"""
LangGraph pipeline — the Newsroom StateGraph.

Graph topology:
    START
      │
      ▼
   scout ──(no topic / error)──► END
      │
      ▼
  journalist ──(error)──► END
      │
      ▼
   editor
      │
      ▼
 fact_checker
      │
      ├── score >= MIN  ──► publisher ──► END
      │
      ├── score < MIN AND revisions < MAX ──► editor (revision loop)
      │
      └── score < MIN AND revisions >= MAX ──► END (rejected)

Checkpointing:
  LangGraph's SqliteSaver is used so runs can be resumed after a crash.
  Each run has a unique `thread_id` (run_id) stored in the state.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import structlog
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

from agents.editor import EditorAgent
from agents.fact_checker import FactCheckerAgent
from agents.journalist import JournalistAgent
from agents.publisher import PublisherAgent
from agents.topic_scout import TopicScoutAgent
from config.overrides import apply_overrides
from config.settings import Settings
from models.schemas import ArticleStatus, NewsroomState, Topic

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Routing function
# ---------------------------------------------------------------------------


def route_after_fact_check(
    state: NewsroomState,
) -> Literal["editor", "publisher", "__end__"]:
    """
    Conditional edge from fact_checker.

    Called by LangGraph to decide the next node.
    The fact_checker already set `state["status"]` — we just read it.
    """
    status = state.get("status", "")
    revision_count = state.get("revision_count", 0)

    if status == ArticleStatus.PUBLISHING.value:
        log.info("graph.route", decision="publish", revision=revision_count)
        return "publisher"

    if status == ArticleStatus.EDITING.value:
        # Increment revision counter before going back to editor
        log.info("graph.route", decision="revise", revision=revision_count + 1)
        return "editor"

    # REJECTED or FAILED
    log.info("graph.route", decision="end_rejected", status=status)
    return END


def route_after_scout(
    state: NewsroomState,
) -> Literal["journalist", "__end__"]:
    """If scout found nothing, stop early."""
    if state.get("topic") is None or state.get("status") == ArticleStatus.FAILED.value:
        return END
    return "journalist"


# ---------------------------------------------------------------------------
# Revision counter node
# ---------------------------------------------------------------------------


def increment_revision(state: NewsroomState) -> dict:
    """
    Thin node inserted between fact_checker and editor (on revision path).
    Increments the revision counter so the editor and fact_checker know
    how many loops have happened.
    """
    return {"revision_count": state.get("revision_count", 0) + 1}


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------


def build_graph(settings: Settings) -> "CompiledGraph":
    """
    Builds and compiles the newsroom pipeline.

    Args:
        settings: Application settings (contains API keys, thresholds, etc.)

    Returns:
        A compiled LangGraph graph ready to invoke.
    """
    # Instantiate agents (each gets its own settings reference)
    scout = TopicScoutAgent(settings)
    journalist = JournalistAgent(settings)
    editor = EditorAgent(settings)
    fact_checker = FactCheckerAgent(settings)
    publisher = PublisherAgent(settings)

    # Build the graph
    graph = StateGraph(NewsroomState)

    # Register nodes
    graph.add_node("scout", scout.run)
    graph.add_node("journalist", journalist.run)
    graph.add_node("editor", editor.run)
    graph.add_node("increment_revision", increment_revision)
    graph.add_node("fact_checker", fact_checker.run)
    graph.add_node("publisher", publisher.run)

    # Edges
    graph.add_edge(START, "scout")

    graph.add_conditional_edges(
        "scout",
        route_after_scout,
        {"journalist": "journalist", END: END},
    )

    graph.add_edge("journalist", "editor")
    graph.add_edge("editor", "fact_checker")

    graph.add_conditional_edges(
        "fact_checker",
        route_after_fact_check,
        {
            "publisher": "publisher",
            "editor": "increment_revision",
            END: END,
        },
    )

    # Revision loop: counter node → back to editor
    graph.add_edge("increment_revision", "editor")

    graph.add_edge("publisher", END)

    # Checkpointing — persists state to SQLite so runs survive crashes
    checkpoints_dir = Path(settings.newsroom_checkpoints_dir)
    checkpoints_dir.mkdir(parents=True, exist_ok=True)
    db_path = str(checkpoints_dir / "newsroom.db")

    import sqlite3

    conn = sqlite3.connect(db_path, check_same_thread=False)
    checkpointer = SqliteSaver(conn)

    compiled = graph.compile(
        checkpointer=checkpointer,
        # Safety net: prevent infinite loops even if routing logic has a bug
        # Max steps = scout(1) + journalist(1) + editor(1..3) + fact_checker(1..3) + publisher(1) + increments
        interrupt_before=None,
    )

    log.info("graph.compiled", checkpoint_db=db_path)
    return compiled


# ---------------------------------------------------------------------------
# Run helper — used by both CLI and API
# ---------------------------------------------------------------------------


def run_pipeline(
    settings: Settings,
    run_id: str,
    custom_topic: str | None = None,
) -> NewsroomState:
    """
    Runs a full newsroom pipeline from scratch.

    Args:
        settings:     Application settings
        run_id:       Unique identifier for this run (used as LangGraph thread_id)
        custom_topic: Optional topic string; skips RSS autodiscovery when set

    Returns:
        Final state after the pipeline completes.
    """
    effective_settings = apply_overrides(settings)
    graph = build_graph(effective_settings)

    initial_topic: Topic | None = None
    if custom_topic:
        from uuid import uuid4
        initial_topic = Topic(
            id=uuid4(),
            title=custom_topic,
            query=custom_topic,
            summary=f"Custom topic: {custom_topic}",
            sources=[],
            category="custom",
        )

    initial_state: NewsroomState = {
        "run_id": run_id,
        "topic": initial_topic,
        "draft": None,
        "edited_article": None,
        "fact_check": None,
        "published_path": None,
        "revision_count": 0,
        "status": ArticleStatus.PENDING.value,
        "errors": [],
        "metrics": None,
    }

    config = {
        "configurable": {"thread_id": run_id},
        # Limit total graph steps as a hard safety cap
        "recursion_limit": 25,
    }

    log.info("pipeline.start", run_id=run_id)

    final_state = graph.invoke(initial_state, config)

    log.info(
        "pipeline.done",
        run_id=run_id,
        status=final_state.get("status"),
        published_path=final_state.get("published_path"),
        errors=final_state.get("errors"),
    )

    return final_state
