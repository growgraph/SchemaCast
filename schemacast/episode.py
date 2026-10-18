"""Episodes: the unit of input — one short text and where it came from."""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from datetime import datetime
from pathlib import Path
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

JsonScalar = str | int | float | bool | None


class ContextItem(BaseModel):
    """An earlier text in the same thread, shown for reference only."""

    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(description="Identifier of the earlier text.")
    text: str = Field(description="Its text.")


class Candidate(BaseModel):
    """A vertex the graph already holds that the text may name.

    Found by the caller (a lookup over the graph), never by SchemaCast; the
    model may pick it by ``cid``, and the pick is checked before it is used.
    """

    model_config = ConfigDict(extra="forbid")

    cid: str = Field(min_length=1, description="Identifier the model picks it by.")
    type: str = Field(min_length=1, description="Its vertex type.")
    key: dict[str, JsonScalar] = Field(
        min_length=1, description="Its identity fields and values."
    )
    label: str | None = Field(default=None, description="The name it was found by.")
    lane: str | None = Field(
        default=None, description="How the caller found it (exact, alias, ...)."
    )


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
    candidates: list[Candidate] = Field(
        default_factory=list,
        description="Existing vertices the text may name; the model may pick one by cid.",
    )

    @model_validator(mode="after")
    def _unique_candidate_ids(self) -> Self:
        seen: set[str] = set()
        for candidate in self.candidates:
            if candidate.cid in seen:
                raise ValueError(f"candidate id {candidate.cid!r} is used twice")
            seen.add(candidate.cid)
        return self


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


def read_candidates(path: str | Path) -> list[Candidate]:
    """Read known entities from a JSON Lines file, one candidate per line.

    Raises:
        EpisodeFileError: on the first line that is not a valid candidate, or a
            ``cid`` used twice.
    """
    path = Path(path)
    found: list[Candidate] = []
    seen: set[str] = set()
    with open(path, encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                candidate = Candidate.model_validate(json.loads(line))
            except json.JSONDecodeError as error:
                raise EpisodeFileError(
                    path, number, f"not JSON: {error.msg}"
                ) from error
            except ValidationError as error:
                raise EpisodeFileError(path, number, str(error)) from error
            if candidate.cid in seen:
                raise EpisodeFileError(
                    path, number, f"cid {candidate.cid!r} is used twice"
                )
            seen.add(candidate.cid)
            found.append(candidate)
    return found


def with_known(
    episodes: Iterable[Episode], known: list[Candidate]
) -> Iterator[Episode]:
    """*episodes*, each without candidates of its own given *known*."""
    for episode in episodes:
        if known and not episode.candidates:
            yield episode.model_copy(update={"candidates": list(known)})
        else:
            yield episode
