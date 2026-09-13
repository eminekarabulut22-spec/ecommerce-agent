from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ToolCallRecord(BaseModel):
    """One tool invocation. `input_summary`/`output_summary` are small, hand-built dicts -
    never raw tool arguments - so secrets (API keys) and bulky data (raw image bytes, full
    LLM responses) never end up in the trace."""

    iteration: int
    tool_name: str
    input_summary: dict[str, Any] = Field(default_factory=dict)
    output_summary: dict[str, Any] = Field(default_factory=dict)
    llm_initiated: bool = False
    error: str | None = None
    started_at: datetime = Field(default_factory=_utcnow)
    finished_at: datetime = Field(default_factory=_utcnow)


class AgentRunRecord(BaseModel):
    run_id: UUID = Field(default_factory=uuid4)
    source_image_url: str
    started_at: datetime = Field(default_factory=_utcnow)
    finished_at: datetime | None = None
    outcome: str | None = None
    iterations_used: int = 0
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)


class TraceRecorder:
    """Accumulates `ToolCallRecord`s for one agent run and produces the final `AgentRunRecord`."""

    def __init__(self, *, source_image_url: str) -> None:
        self.record = AgentRunRecord(source_image_url=source_image_url)

    def log_tool_call(
        self,
        *,
        iteration: int,
        tool_name: str,
        input_summary: dict[str, Any] | None = None,
        output_summary: dict[str, Any] | None = None,
        llm_initiated: bool = False,
        error: str | None = None,
    ) -> None:
        self.record.tool_calls.append(
            ToolCallRecord(
                iteration=iteration,
                tool_name=tool_name,
                input_summary=input_summary or {},
                output_summary=output_summary or {},
                llm_initiated=llm_initiated,
                error=error,
            )
        )

    def finish(self, *, outcome: str, iterations_used: int) -> AgentRunRecord:
        self.record.finished_at = _utcnow()
        self.record.outcome = outcome
        self.record.iterations_used = iterations_used
        return self.record
