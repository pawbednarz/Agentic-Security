"""
FastAPI application for the Newsroom pipeline.

Endpoints:
  POST /api/v1/articles/generate  — Start a new pipeline run (async, background task)
  GET  /api/v1/runs/{run_id}      — Get run status + result
  GET  /api/v1/articles           — List all published articles from manifest

Security:
  - Bearer token authentication on all endpoints
  - Request size limit (prevents huge payloads)
  - CORS configured conservatively
  - No sensitive data in error responses
"""

from __future__ import annotations

import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

import structlog
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from api.auth import verify_token
from api.config_router import router as config_router
from config.logging_setup import configure_logging
from config.settings import Settings, get_settings
from config.tracing import configure_tracing
from models.schemas import ArticleStatus, RunMetrics
from orchestrator.graph import run_pipeline

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# In-memory run registry (keyed by run_id → final NewsroomState dict)
# In production: replace with Redis or a proper database.
# ---------------------------------------------------------------------------
_runs: dict[str, dict] = {}
_executor = ThreadPoolExecutor(max_workers=4)

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Newsroom API",
    description="Multi-agent newsroom pipeline powered by Claude + LangGraph",
    version="1.0.0",
    docs_url="/docs",
    redoc_url=None,
)

# CORS — restrict origins in production
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:8000"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)

app.include_router(config_router)

# Serve the web GUI at /ui/
_ui_dir = Path(__file__).parent.parent / "ui"
if _ui_dir.exists():
    app.mount("/ui", StaticFiles(directory=str(_ui_dir), html=True), name="ui")

# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class GenerateRequest(BaseModel):
    """Optional: provide a specific topic query instead of RSS autodiscovery."""
    query: Optional[str] = None


class RunStatusResponse(BaseModel):
    run_id: str
    status: str
    published_path: Optional[str] = None
    errors: list[str] = []
    article_title: Optional[str] = None
    article_word_count: Optional[int] = None
    fact_score: Optional[float] = None


class ArticleListItem(BaseModel):
    id: str
    title: str
    file: str
    word_count: int
    revision: int
    published_at: str


# ---------------------------------------------------------------------------
# Background task
# ---------------------------------------------------------------------------


def _run_in_background(run_id: str, settings: Settings, custom_topic: Optional[str] = None) -> None:
    """
    Runs the pipeline synchronously in a thread pool.
    Stores the result in `_runs` when done.
    """
    try:
        _runs[run_id] = {"status": ArticleStatus.RESEARCHING.value, "run_id": run_id}
        final_state = run_pipeline(settings, run_id, custom_topic=custom_topic)
        _runs[run_id] = dict(final_state)
    except Exception as e:
        log.error("api.run_failed", run_id=run_id, error=str(e))
        _runs[run_id] = {
            "run_id": run_id,
            "status": ArticleStatus.FAILED.value,
            "errors": [str(e)],
        }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/", include_in_schema=False)
async def root_redirect():
    return RedirectResponse(url="/ui/index.html")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post(
    "/api/v1/articles/generate",
    status_code=202,
    dependencies=[Depends(verify_token)],
)
async def generate_article(
    request: GenerateRequest,
    background_tasks: BackgroundTasks,
    settings: Settings = Depends(get_settings),
) -> dict:
    """
    Starts a new article generation run.
    Returns immediately with a `run_id` — poll `/api/v1/runs/{run_id}` for status.
    """
    run_id = str(uuid.uuid4())
    _runs[run_id] = {"status": ArticleStatus.PENDING.value, "run_id": run_id}

    # Run in a background thread (LangGraph is sync)
    background_tasks.add_task(_run_in_background, run_id, settings, request.query)

    log.info("api.generate_requested", run_id=run_id)
    return {"run_id": run_id, "status": "accepted"}


@app.get(
    "/api/v1/runs/{run_id}",
    response_model=RunStatusResponse,
    dependencies=[Depends(verify_token)],
)
async def get_run_status(run_id: str) -> RunStatusResponse:
    """Returns the current status and result of a pipeline run."""
    state = _runs.get(run_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Run not found")

    article = state.get("edited_article")
    fact_check = state.get("fact_check")

    return RunStatusResponse(
        run_id=run_id,
        status=state.get("status", "unknown"),
        published_path=state.get("published_path"),
        errors=state.get("errors", []),
        article_title=article.title if article else None,
        article_word_count=article.word_count if article else None,
        fact_score=fact_check.overall_score if fact_check else None,
    )


@app.get(
    "/api/v1/articles",
    response_model=list[ArticleListItem],
    dependencies=[Depends(verify_token)],
)
async def list_articles(settings: Settings = Depends(get_settings)) -> list[ArticleListItem]:
    """Returns all published articles from the output manifest."""
    manifest_path = Path(settings.newsroom_output_dir) / "manifest.json"
    if not manifest_path.exists():
        return []

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        return [ArticleListItem(**item) for item in manifest.get("articles", [])]
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        log.error("api.manifest_error", error=str(e))
        raise HTTPException(status_code=500, detail="Could not read article manifest")


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------


@app.on_event("startup")
async def startup() -> None:
    settings = get_settings()
    configure_logging(level=settings.log_level, fmt=settings.log_format)
    configure_tracing(settings)
    log.info("api.startup", host=settings.api_host, port=settings.api_port)
