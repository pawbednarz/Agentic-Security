# Newsroom — Multi-Agent AI Pipeline

Autonomiczna redakcja newsroomu zbudowana na **LangGraph** + **Claude Sonnet 4.5**.

```
Topic Scout → Journalist → Editor → Fact Checker → Publisher
                                ↑______________|
                              (revision loop, max 2x)
```

## Architektura

```mermaid
flowchart LR
    START((START)) --> Scout

    Scout["TOPIC SCOUT<br/>Odkrywa temat z RSS<br/>status → WRITING"]
    Journalist["JOURNALIST<br/>Pisze draft artykułu<br/>status → EDITING"]
    Editor["EDITOR<br/>Poprawia styl i klarowność<br/>status → FACT_CHECKING"]
    FC["FACT CHECKER<br/>Weryfikuje do 5 twierdzeń<br/>score 0.0–1.0 · próg: 0.7"]
    Publisher["PUBLISHER<br/>Zapisuje .md + manifest<br/>status → PUBLISHED"]

    Scout --> Journalist
    Scout -->|brak tematu| FAILED([FAILED])
    Journalist --> Editor
    Editor --> FC
    FC -->|"score >= 0.7"| Publisher
    FC -->|"score < 0.7, revisions < 2"| Editor
    FC -->|"score < 0.7, revisions >= 2"| REJECTED([REJECTED])
    Publisher --> DONE((END))

    RSS[(RSS Feeds)] -.->|feedparser| Scout
    WebSearch[(Tavily / DuckDuckGo)] -.->|szukaj tematu| Scout
    WebSearch -.->|weryfikacja twierdzeń| FC

    style Scout    fill:#2563EB,color:#fff,stroke:#1D4ED8
    style Journalist fill:#7C3AED,color:#fff,stroke:#6D28D9
    style Editor   fill:#D97706,color:#fff,stroke:#B45309
    style FC       fill:#DC2626,color:#fff,stroke:#B91C1C
    style Publisher fill:#059669,color:#fff,stroke:#047857
    style FAILED   fill:#374151,color:#9CA3AF,stroke:#4B5563
    style REJECTED fill:#7F1D1D,color:#FCA5A5,stroke:#991B1B
    style START    fill:#1E293B,color:#fff,stroke:#0F172A
    style DONE     fill:#059669,color:#fff,stroke:#047857
    style RSS      fill:#DBEAFE,color:#1E40AF,stroke:#93C5FD
    style WebSearch fill:#DBEAFE,color:#1E40AF,stroke:#93C5FD
```

Każdy węzeł operuje na współdzielonym `NewsroomState` (LangGraph `TypedDict`):
`topic · draft · edited_article · fact_check · published_path · revision_count · status · errors · metrics`

## Wymagania

- Python 3.11+
- Klucz API Anthropic (wymagany)
- Klucz API Tavily (opcjonalny — fallback do DuckDuckGo)

## Szybki start

```bash
# 1. Skopiuj i wypełnij konfigurację
cp .env.example .env
# Edytuj .env — dodaj ANTHROPIC_API_KEY

# 2. Zainstaluj zależności
pip install -r requirements.txt

# 3. Sprawdź konfigurację
python main.py config

# 4. Uruchom pipeline
python main.py run

# 5. Lub uruchom API
python main.py serve
```

## Struktura projektu

```
newsroom/
├── agents/          # Każdy agent jako osobny moduł
│   ├── base.py      # Wspólna infrastruktura (LLM, logging, rate limiting)
│   ├── topic_scout.py
│   ├── journalist.py
│   ├── editor.py
│   ├── fact_checker.py
│   └── publisher.py
├── tools/
│   ├── search.py    # Tavily + DuckDuckGo fallback
│   └── rss.py       # RSS feed reader
├── models/
│   └── schemas.py   # Pydantic modele + LangGraph state
├── orchestrator/
│   └── graph.py     # LangGraph StateGraph + routing
├── config/
│   ├── settings.py  # pydantic-settings (wszystkie zmienne z .env)
│   └── logging_setup.py
├── api/
│   └── app.py       # FastAPI (Bearer auth, background tasks)
├── tests/           # pytest, wszystkie LLM wywołania mockowane
├── output/          # Wygenerowane artykuły (.md)
├── checkpoints/     # SQLite checkpoints LangGraph
└── main.py          # CLI: run | serve | config
```

## API

Po uruchomieniu `python main.py serve`:

```bash
# Wygeneruj artykuł (async)
curl -X POST http://localhost:8000/api/v1/articles/generate \
  -H "Authorization: Bearer change_me_before_deploy" \
  -H "Content-Type: application/json" \
  -d '{}'

# Sprawdź status
curl http://localhost:8000/api/v1/runs/{run_id} \
  -H "Authorization: Bearer change_me_before_deploy"

# Lista artykułów
curl http://localhost:8000/api/v1/articles \
  -H "Authorization: Bearer change_me_before_deploy"
```

Dokumentacja Swagger: http://localhost:8000/docs

## Uruchamianie testów

```bash
pytest tests/ -v
```

## Bezpieczeństwo

| Zagrożenie | Zabezpieczenie |
|---|---|
| Prompt injection | Zewnętrzna treść owijana w `<external_content>` tagi |
| Wyciek sekretów | `SecretStr` w Pydantic, `.env` w `.gitignore` |
| Pętle nieskończone | `recursion_limit=25` w LangGraph + max revisji |
| Niekontrolowane koszty | Rate limiter, cap na tokeny, max 5 źródeł/agenta |
| Złośliwy output LLM | Walidacja Pydantic na każdym wyjściu agenta |
| Nieautoryzowany dostęp API | Bearer token authentication |

## Konfiguracja RSS

Domyślnie: BBC World + NYT World.
Edytuj `RSS_FEEDS` w `.env` (oddzielone przecinkami):

```env
RSS_FEEDS=https://feeds.bbci.co.uk/news/world/rss.xml,https://rss.nytimes.com/services/xml/rss/nyt/World.xml
```
