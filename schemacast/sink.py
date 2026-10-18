"""Quarantine: what a cast could not write, with enough to review it.

The same shape as graflo's ``DocErrorSink``: a small async protocol and a JSON
Lines implementation. Three record kinds share one file — ``finding`` (an item
left out after repairs), ``episode`` (an episode that produced nothing, e.g. no
schema matched or the reply never parsed) and ``unmapped`` (what the model saw
and no type covers: proposals for the schema, not failures).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field


class QuarantineRecord(BaseModel):
    """One quarantined item."""

    record: Literal["finding", "episode", "unmapped"]
    source_id: str
    kind: str = Field(
        description="Finding kind, episode failure kind, or ``unmapped``."
    )
    detail: str | None = None
    path: str | None = Field(default=None, description="Location in the reply.")
    message: str = ""
    quote: str | None = None
    char_start: int | None = None
    char_end: int | None = None
    fragment: dict[str, Any] | None = Field(
        default=None, description="The item as the model wrote it."
    )
    attempts: int = Field(default=0, description="Model requests made for the episode.")
    raw_output: str | None = Field(
        default=None, description="The reply the item came from."
    )
    schema_ref: str | None = None


@runtime_checkable
class QuarantineSink(Protocol):
    """Where quarantined records go."""

    async def write_records(self, records: list[QuarantineRecord]) -> None:
        """Persist *records*; safe to call from concurrent tasks."""


class JsonlQuarantineSink:
    """Appends records to a JSON Lines file."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = asyncio.Lock()

    async def write_records(self, records: list[QuarantineRecord]) -> None:
        if not records:
            return

        def _append() -> None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as handle:
                handle.writelines(
                    record.model_dump_json(exclude_none=True) + "\n"
                    for record in records
                )

        async with self._lock:
            await asyncio.to_thread(_append)


class MemoryQuarantineSink:
    """Keeps records in memory; the default when no file is given."""

    def __init__(self) -> None:
        self.records: list[QuarantineRecord] = []

    async def write_records(self, records: list[QuarantineRecord]) -> None:
        self.records.extend(records)
