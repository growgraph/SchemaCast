"""Episodes: the unit of input — one short text and where it came from."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

JsonScalar = str | int | float | bool | None
KeyValue = str | int | float | bool

#: Known entities an episode may carry, at most.
MAX_KNOWN = 8


class ContextItem(BaseModel):
    """An earlier text in the same thread, shown for reference only."""

    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(description="Identifier of the earlier text.")
    text: str = Field(description="Its text.")


class KnownEntity(BaseModel):
    """An entity the text belongs to or is about, identified by its source.

    The model may relate to it by ref but never adds it as a vertex or states
    its properties: its identity comes from the source, never from the text.
    """

    model_config = ConfigDict(extra="forbid")

    vertex_type: str = Field(min_length=1, description="Its vertex type.")
    key: dict[str, KeyValue] = Field(
        min_length=1,
        description="Its primary identity: every identity field of the type and "
        "nothing else, or it is not shown.",
    )
    role: str = Field(
        pattern=r"^[a-z][a-z_]*$",
        description="What it is to the text, e.g. ``subject``: the record the "
        "text belongs to.",
    )
    label: str | None = Field(default=None, description="A name shown to the model.")

    @field_validator("key")
    @classmethod
    def _no_empty_values(cls, key: dict[str, KeyValue]) -> dict[str, KeyValue]:
        empty = [name for name, value in key.items() if value is None or value == ""]
        if empty:
            raise ValueError(f"key fields without a value: {', '.join(empty)}")
        return key


class Episode(BaseModel):
    """One text to cast.

    The structured half of a record — author, channel, thread — belongs in
    ``metadata`` and is written by an ordinary GraFlo resource, never read off
    the text by the model; ``metadata`` is not shown to the model.
    """

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, description="The text to cast.")
    source_id: str = Field(
        min_length=1, description="Stable identifier of the source record."
    )
    reference_time: datetime | None = Field(
        default=None,
        description="When the text was written; resolves relative dates.",
    )
    metadata: dict[str, JsonScalar] = Field(
        default_factory=dict, description="Source metadata, never shown to the model."
    )
    context: list[ContextItem] = Field(
        default_factory=list,
        description="Preceding texts in the thread, for resolving references only.",
    )
    known: list[KnownEntity] = Field(
        default_factory=list,
        description=f"Entities the source names, at most {MAX_KNOWN}; an edge the "
        "text states may end at one.",
    )

    @field_validator("known")
    @classmethod
    def _distinct_known(cls, known: list[KnownEntity]) -> list[KnownEntity]:
        if len(known) > MAX_KNOWN:
            raise ValueError(f"at most {MAX_KNOWN} known entities, got {len(known)}")
        seen: set[tuple[str, tuple[tuple[str, str], ...]]] = set()
        for entity in known:
            identity = (
                entity.vertex_type,
                tuple(sorted((k, repr(v)) for k, v in entity.key.items())),
            )
            if identity in seen:
                raise ValueError(
                    f"{entity.vertex_type} {entity.key} is given more than once"
                )
            seen.add(identity)
        return known


class EpisodeFileError(ValueError):
    """A line of an episodes file that is not a valid episode."""

    def __init__(self, path: Path, line: int, reason: str) -> None:
        super().__init__(f"{path}:{line}: {reason}")
        self.path = path
        self.line = line


def read_episodes(path: str | Path) -> Iterator[Episode]:
    """Read episodes from a JSON Lines file, one object per line.

    Blank lines are skipped.

    Raises:
        EpisodeFileError: on the first line that is not a valid episode.
    """
    path = Path(path)
    with open(path, encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                yield Episode.model_validate(json.loads(line))
            except json.JSONDecodeError as error:
                raise EpisodeFileError(
                    path, number, f"not JSON: {error.msg}"
                ) from error
            except ValidationError as error:
                raise EpisodeFileError(path, number, str(error)) from error
