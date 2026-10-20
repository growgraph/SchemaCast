"""Episodes: the unit of input — one short text and where it came from."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

JsonScalar = str | int | float | bool | None


class ContextItem(BaseModel):
    """An earlier text in the same thread, shown for reference only."""

    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(description="Identifier of the earlier text.")
    text: str = Field(description="Its text.")


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
