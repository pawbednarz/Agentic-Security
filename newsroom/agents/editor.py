"""
Editor Agent

Responsibilities:
1. Receive the journalist's draft (or a revision-flagged article from fact-checking)
2. Improve: clarity, flow, structure, language quality
3. Return a polished article with notes for the fact checker

The editor is also called on revision loops — it receives `editor_notes`
from the fact checker explaining what needs to be fixed.
"""

from __future__ import annotations

import structlog
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from agents.base import BaseAgent
from config.settings import Settings
from models.schemas import Article, ArticleStatus, NewsroomState

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Structured output schema
# ---------------------------------------------------------------------------


class EditedArticle(BaseModel):
    title: str = Field(description="Possibly improved title")
    lead: str = Field(description="Improved opening paragraph")
    body: str = Field(description="Improved main body")
    conclusion: str = Field(description="Improved conclusion")
    sources: list[str] = Field(description="Keep original sources list")
    editor_notes: str = Field(
        description=(
            "Notes for the fact checker: "
            "list any claims that should be verified, "
            "any vague statements you flagged, "
            "and any changes you made that need verification."
        )
    )
    changes_summary: str = Field(
        description="Brief description of what you changed and why"
    )


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


class EditorAgent(BaseAgent):
    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self._lang_name = settings.language_name()

    def run(self, state: NewsroomState) -> dict:
        return self._timed_run("editor", self._execute, state)

    def _execute(self, state: NewsroomState) -> dict:
        errors = list(state.get("errors", []))

        # Use the latest available article: edited > draft
        article = state.get("edited_article") or state.get("draft")
        revision_count = state.get("revision_count", 0)

        if article is None:
            errors.append("Editor: no article found in state")
            return {"status": ArticleStatus.FAILED.value, "errors": errors}

        # If this is a revision, include fact-check issues in the prompt
        fact_check = state.get("fact_check")
        revision_instructions = ""
        if fact_check and fact_check.issues:
            issues_list = "\n".join(f"- {issue}" for issue in fact_check.issues)
            revision_instructions = (
                f"\n\n**THIS IS REVISION #{revision_count}.**\n"
                f"The fact-checker found the following issues that MUST be addressed:\n"
                f"{issues_list}\n"
                f"Fix these issues. If a claim cannot be verified from available sources, "
                f"soften the language (e.g., 'reportedly', 'according to sources') or remove it."
            )

        system = self._system_prompt()
        user = self._user_prompt(article, revision_instructions)

        try:
            llm = self.structured_llm(EditedArticle)
            edited_raw: EditedArticle = llm.invoke(
                [SystemMessage(content=system), HumanMessage(content=user)]
            )
        except Exception as e:
            log.error("editor.llm_error", error=str(e))
            errors.append(f"Editor LLM error: {e}")
            return {"status": ArticleStatus.FAILED.value, "errors": errors}

        try:
            edited = Article(
                id=article.id,          # Keep the same ID across revisions
                topic_id=article.topic_id,
                title=edited_raw.title,
                lead=edited_raw.lead,
                body=edited_raw.body,
                conclusion=edited_raw.conclusion,
                sources=edited_raw.sources or article.sources,
                revision=revision_count + 1,
                editor_notes=edited_raw.editor_notes,
            )
        except Exception as e:
            log.error("editor.validation_error", error=str(e))
            errors.append(f"Edited article validation failed: {e}")
            return {"status": ArticleStatus.FAILED.value, "errors": errors}

        log.info(
            "editor.done",
            title=edited.title,
            revision=edited.revision,
            word_count=edited.word_count,
            changes=edited_raw.changes_summary[:100],
        )

        return {
            "edited_article": edited,
            "status": ArticleStatus.FACT_CHECKING.value,
            "errors": errors,
        }

    # ------------------------------------------------------------------
    # Prompts
    # ------------------------------------------------------------------

    def _system_prompt(self) -> str:
        return (
            f"You are a senior news editor. The article is written in {self._lang_name}. "
            "Your job is to improve the article while preserving all factual content. "
            "Rules:\n"
            f"1. Keep the article in {self._lang_name}.\n"
            "2. Do NOT add facts that are not in the original — only reorganise and clarify.\n"
            "3. Improve sentence flow, remove redundancy, fix grammar.\n"
            "4. Ensure the lead answers Who/What/When/Where.\n"
            "5. Each paragraph should have a clear focus.\n"
            "6. Flag any claims in editor_notes that the fact-checker should verify.\n"
            "7. If fixing a revision: address ALL the issues listed explicitly."
        )

    def _user_prompt(self, article: Article, revision_instructions: str) -> str:
        return (
            f"Please edit the following article:{revision_instructions}\n\n"
            f"---\n"
            f"TITLE: {article.title}\n\n"
            f"LEAD:\n{article.lead}\n\n"
            f"BODY:\n{article.body}\n\n"
            f"CONCLUSION:\n{article.conclusion}\n\n"
            f"SOURCES: {', '.join(article.sources)}\n"
            f"---\n\n"
            "Return the edited version now."
        )
