# Newsroom — Multi-Agent AI Pipeline

Autonomiczna redakcja newsroomu zbudowana na **LangGraph** + **Claude Sonnet 4.5**.

```
Topic Scout → Journalist → Editor → Fact Checker → Publisher
                                ↑______________|
                              (revision loop, max 2x)
```

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
