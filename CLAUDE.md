# CLAUDE.md — Agentic Security / Newsroom

This file gives AI assistants the context needed to work effectively in this repository.

## What This Project Is

An autonomous **multi-agent AI newsroom** pipeline that discovers news topics, writes articles, edits them, fact-checks claims, and publishes to disk. It is built on LangGraph + LangChain using Claude (Anthropic) as the underlying LLM.

**Tech stack:**
- Python 3.11+
- LangGraph (stateful agent orchestration) + LangChain
- Claude Sonnet (`claude-sonnet-4-5` default model)
- FastAPI (REST API + web dashboard)
- Pydantic v2 (settings + domain models)
- structlog (structured logging)
- pytest + pytest-asyncio + pytest-mock (testing)
- Tavily (web search, primary) + DuckDuckGo (free fallback)
- feedparser (RSS ingestion)
- LangSmith (optional observability)

---

## Repository Layout

```
Agentic-Security/
└── newsroom/               # All application code lives here
    ├── agents/             # One file per agent in the pipeline
    │   ├── base.py         # BaseAgent shared infrastructure
    │   ├── topic_scout.py  # RSS → topic selection
    │   ├── journalist.py   # Topic → article draft
    │   ├── editor.py       # Draft → polished article (handles revision loops)
    │   ├── fact_checker.py # Article → claim verification
    │   └── publisher.py    # Saves article as Markdown with YAML front-matter
    ├── api/                # FastAPI application
    │   ├── app.py          # Endpoints: /api/v1/articles/generate, /runs/{id}, /articles
    │   ├── auth.py         # Bearer token authentication
    │   └── config_router.py# Settings and prompt-override endpoints
    ├── config/             # Configuration management
    │   ├── settings.py     # Pydantic BaseSettings — single source of truth
    │   ├── logging_setup.py# structlog console/JSON config
    │   ├── prompts.py      # Prompt registry with default + user overrides
    │   ├── tracing.py      # LangSmith integration
    │   └── overrides.py    # Runtime config overrides (persisted to JSON)
    ├── models/
    │   └── schemas.py      # Domain models + LangGraph NewsroomState TypedDict
    ├── orchestrator/
    │   └── graph.py        # LangGraph StateGraph, routing logic, run_pipeline()
    ├── tools/
    │   ├── search.py       # SearchTool (Tavily + DuckDuckGo fallback, rate-limited)
    │   └── rss.py          # RssReader (feedparser, age-filtered)
    ├── tests/
    │   ├── conftest.py     # pytest fixtures (settings, domain objects, state)
    │   ├── test_agents.py  # Agent unit tests (LLM mocked)
    │   ├── test_graph.py   # Routing/conditional-edge tests
    │   └── test_tools.py   # Search and RSS tool tests
    ├── ui/
    │   └── index.html      # Vue.js web dashboard
    ├── main.py             # CLI entry point (run / serve / config)
    ├── requirements.txt    # Python dependencies
    ├── .env.example        # Environment variable template
    └── .gitignore
```

---

## Pipeline Flow

```
START
  │
  ▼
Topic Scout  ──(no topic found)──► END (failed)
  │
  ▼
Journalist
  │
  ▼
Editor  ◄──────────────────────────────────┐
  │                                         │
  ▼                                         │
Fact Checker ──(score < threshold)──► Editor (revision loop, max 2×)
  │
  ├── (score ≥ threshold) ──► Publisher ──► END (published)
  │
  └── (max revisions exceeded) ──► END (rejected)
```

State flows through `NewsroomState` (TypedDict in `models/schemas.py`). LangGraph persists checkpoints to SQLite in `checkpoints/`.

---

## Development Commands

All commands run from the `newsroom/` directory:

```bash
# Install dependencies
pip install -r requirements.txt

# Copy and fill in .env
cp .env.example .env

# Verify configuration (secrets masked)
python main.py config

# Run one full pipeline cycle
python main.py run

# Start the FastAPI server (default: http://localhost:8000)
python main.py serve

# Run all tests
pytest tests/ -v

# Run a specific test file
pytest tests/test_agents.py -v
```

No Makefile exists. No CI/CD pipeline is configured yet.

---

## Environment Variables

All configuration is managed through environment variables loaded via Pydantic BaseSettings (`config/settings.py`). Copy `.env.example` to `.env` and populate the values.

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | **Yes** | — | Claude API key |
| `TAVILY_API_KEY` | No | — | Web search (DuckDuckGo used if absent) |
| `NEWSROOM_LANGUAGE` | No | `pl` | Article language (`pl` or `en`) |
| `NEWSROOM_MAX_WORDS` | No | `900` | Max article word count |
| `NEWSROOM_MIN_WORDS` | No | `400` | Min article word count |
| `NEWSROOM_MAX_REVISIONS` | No | `2` | Max revision loops before rejection |
| `NEWSROOM_MIN_FACT_SCORE` | No | `0.7` | Minimum fact-check score (0.0–1.0) |
| `NEWSROOM_OUTPUT_DIR` | No | `output` | Where published articles are saved |
| `NEWSROOM_CHECKPOINTS_DIR` | No | `checkpoints` | LangGraph SQLite checkpoints |
| `API_SECRET_KEY` | No | `change_me_before_deploy` | Bearer token for FastAPI auth |
| `API_HOST` | No | `0.0.0.0` | API bind host |
| `API_PORT` | No | `8000` | API port |
| `RSS_FEEDS` | No | BBC + NYT | Comma-separated RSS feed URLs |
| `LANGSMITH_API_KEY` | No | — | Enables LangSmith tracing if set |
| `LANGSMITH_PROJECT` | No | `newsroom` | LangSmith project name |
| `LOG_LEVEL` | No | `INFO` | Log level |
| `LOG_FORMAT` | No | `console` | `console` (dev) or `json` (prod) |
| `LLM_MODEL` | No | `claude-sonnet-4-5` | Claude model ID |
| `LLM_MAX_TOKENS` | No | `4096` | Max tokens per LLM call |
| `LLM_TIMEOUT_SECONDS` | No | `60` | LLM call timeout |
| `LLM_MAX_RETRIES` | No | `3` | LLM retry count |

**Important:** `RSS_FEEDS` in `.env` is comma-separated. Pydantic settings handle parsing. Empty strings are treated as absent values.

---

## Code Conventions

### Naming
- `snake_case` — functions, variables, module names
- `PascalCase` — classes, Pydantic models, enums
- `SCREAMING_SNAKE_CASE` — module-level constants
- Prompt keys use dot notation: `topic_scout.select`, `journalist.write`

### Agent Pattern
All agents inherit `BaseAgent` (`agents/base.py`) and implement:
```python
def run(self, state: NewsroomState) -> dict:
    ...
```
Agents return a **partial state dict** (only keys they update). LangGraph merges this into the full state.

### LLM Calls
Use `.with_structured_output(SomePydanticModel)` for all structured data extraction. Raw string outputs are avoided. This is wired through `BaseAgent.structured_llm()`.

### State Updates
`NewsroomState` uses `TypedDict` with `total=False` so agents only need to return the keys they change. The error list (`state["errors"]`) is accumulated, never overwritten.

### Error Handling
- Catch exceptions inside agents, append to `state["errors"]`, log with structlog, and return a partial state
- Do not let exceptions propagate out of `run()` — the graph handles routing based on state contents

### Logging
Use structlog in every module:
```python
import structlog
log = structlog.get_logger(__name__)
log.info("agent.done", run_id=run_id, words=article.word_count)
```
Structured key=value context is mandatory. Never use `print()` for observability.

### Security
- API keys use `SecretStr` — they are **never** logged or included in `repr()`
- External content (RSS, web search results) is always wrapped in `<external_content>` XML tags in prompts
- `SearchResult.sanitize_content()` strips prompt injection patterns and caps content at 8000 chars
- System prompts include explicit instructions to ignore instructions found in external data

### Type Hints
Every function must have complete type annotations. Pydantic validates all domain objects at construction time (fail fast).

### Docstrings
Module-level and class-level docstrings are present throughout. Follow the existing Google-style format.

---

## Testing

All LLM calls are mocked — tests never make real API requests. Use `pytest-mock` with `mocker.patch`:

```python
def test_journalist_run(mocker, sample_state):
    mock_llm = mocker.patch("agents.journalist.ChatAnthropic")
    mock_llm.return_value.with_structured_output.return_value.invoke.return_value = ...
```

Key fixtures in `tests/conftest.py`:
- `test_settings` — settings with dummy API keys (`monkeypatch`)
- `sample_topic` — a `Topic` object
- `sample_article` — an `Article` object
- `sample_state` — a full `NewsroomState` dict
- `sample_fact_check` — a `FactCheckResult` object

Always clear the settings LRU cache between tests that modify env vars:
```python
from config.settings import get_settings
get_settings.cache_clear()
```

---

## API Endpoints

Base URL: `http://localhost:8000`

All `/api/v1/` routes require `Authorization: Bearer <API_SECRET_KEY>`.

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/v1/articles/generate` | Trigger a new pipeline run (async) |
| `GET` | `/api/v1/runs/{run_id}` | Get run status and result |
| `GET` | `/api/v1/articles` | List published articles |
| `GET` | `/api/v1/config/settings` | Get current settings |
| `PUT` | `/api/v1/config/settings` | Override runtime settings |
| `GET` | `/api/v1/config/prompts` | List prompt overrides |
| `PUT` | `/api/v1/config/prompts/{key}` | Override a prompt |
| `DELETE` | `/api/v1/config/prompts/{key}` | Remove a prompt override |

Interactive docs: `http://localhost:8000/docs`

---

## Published Output Format

Articles are saved to `output/` as Markdown files with YAML front-matter:

```markdown
---
title: "Article Title"
slug: "article-title-2024-01-15"
language: pl
word_count: 750
revision: 1
fact_score: 0.85
published_at: "2024-01-15T12:34:56"
sources:
  - https://example.com/source1
---

Article body...
```

A `manifest.json` file in `output/` is updated after each publication and tracks all published articles.

---

## Runtime Overrides

Settings and prompts can be overridden at runtime without restarting the server:

- **Config overrides** are persisted to `data/config_overrides.json`
- **Prompt overrides** are persisted to `data/prompt_overrides.json`
- Overrides take effect immediately via `apply_overrides()` in `config/overrides.py`

---

## Key Files to Understand First

When working on this codebase, read these files first in order:

1. `models/schemas.py` — all domain types and `NewsroomState`
2. `config/settings.py` — all configuration knobs
3. `agents/base.py` — shared agent infrastructure
4. `orchestrator/graph.py` — pipeline wiring and routing logic
5. The relevant agent file for the area you're working on

---

## What Not To Do

- Do not use `print()` for logging — use structlog
- Do not log `SecretStr` values — Pydantic masks them but be explicit
- Do not hardcode API keys or model names — use `settings.*`
- Do not let agent exceptions propagate out of `run()` — catch and accumulate in `state["errors"]`
- Do not make real LLM or search calls in tests — mock everything
- Do not commit `.env` files — they are in `.gitignore`
- Do not modify `NewsroomState` fields directly from outside agents — state flows through LangGraph transitions only

---

## Git Conventions

Commits follow the **Conventional Commits** format:

```
feat(component): short description
fix(component): short description
docs: short description
refactor(component): short description
test(component): short description
```

Current active branch: `claude/add-claude-documentation-co5Yq`
