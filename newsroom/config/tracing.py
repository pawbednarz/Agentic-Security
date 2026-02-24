"""
LangSmith tracing configuration.

LangChain/LangGraph automatycznie wysyła trace'y do LangSmith jeśli
w os.environ są ustawione odpowiednie zmienne.

Problem: pydantic-settings czyta .env do obiektu Settings,
ale NIE zapisuje wartości z powrotem do os.environ.
Ta funkcja robi to ręcznie — musi być wywołana raz na starcie
(w main.py i api/app.py), po załadowaniu Settings.

Jeśli klucz nie jest ustawiony, tracing jest po prostu wyłączony
— żaden błąd, żadne wyjątki.
"""

from __future__ import annotations

import os

import structlog

log = structlog.get_logger(__name__)


def configure_tracing(settings) -> bool:
    """
    Włącza LangSmith tracing jeśli klucz jest dostępny.

    Returns:
        True jeśli tracing został włączony, False jeśli wyłączony.
    """
    if not settings.has_langsmith():
        # Upewnij się że tracing jest wyłączony (na wypadek gdyby
        # była ustawiona zmienna z poprzedniego procesu)
        os.environ["LANGCHAIN_TRACING_V2"] = "false"
        log.info("tracing.disabled", reason="LANGSMITH_API_KEY not set")
        return False

    api_key = settings.langsmith_api_key.get_secret_value()

    # LangChain rozpoznaje oba warianty nazwy klucza
    os.environ["LANGCHAIN_API_KEY"] = api_key
    os.environ["LANGSMITH_API_KEY"] = api_key
    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    os.environ["LANGCHAIN_PROJECT"] = settings.langsmith_project
    os.environ["LANGCHAIN_ENDPOINT"] = "https://api.smith.langchain.com"

    log.info(
        "tracing.enabled",
        project=settings.langsmith_project,
        url=f"https://smith.langchain.com/projects/{settings.langsmith_project}",
    )
    return True
