"""One extraction call: an episode and a slice in, the model's flat reply out.

The reply is parsed into the wire models below and nothing more. Whether a
type exists, a property belongs to its vertex, a value fits its field or a
quote is in the text are checked afterwards; the wire models only make sure
the reply has the flat shape.
"""

from __future__ import annotations

import math
from typing import Any

from graflo.architecture.schema.context import instance_json_schema
from graflo.architecture.schema.document import Schema
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from schemacast.episode import Episode
from schemacast.llm.client import JsonCompletion, LLMClient
from schemacast.prompt import build_messages

#: JSON Schema name sent with strict output requests.
INSTANCE_SCHEMA_NAME = "lpg_instance"
#: Characters per estimated token, for the episode ceiling.
CHARS_PER_TOKEN = 4

Scalar = str | int | float | bool


class WireProp(BaseModel):
    """A property the model read: name, value as written, and its quote."""

    model_config = ConfigDict(extra="ignore")

    name: str
    value: Scalar | list[Scalar]
    quote: str = ""


class WireVertex(BaseModel):
    """A vertex the model read."""

    model_config = ConfigDict(extra="ignore")

    ref: str
    type: str
    quote: str = ""
    mention: bool = False
    props: list[WireProp] = Field(default_factory=list)


class WireEdge(BaseModel):
    """An edge the model read, between two refs."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    relation: str | None = None
    from_: str = Field(alias="from")
    to: str
    quote: str = ""
    props: list[WireProp] = Field(default_factory=list)


class WireUnmapped(BaseModel):
    """Something the model saw and could not type."""

    model_config = ConfigDict(extra="ignore")

    quote: str
    note: str = ""


class WireExtraction(BaseModel):
    """The model's whole reply. Missing lists read as empty; unknown keys are ignored."""

    model_config = ConfigDict(extra="ignore")

    vertices: list[WireVertex] = Field(default_factory=list)
    edges: list[WireEdge] = Field(default_factory=list)
    unmapped: list[WireUnmapped] = Field(default_factory=list)


class Extraction(BaseModel):
    """One episode's reply, with how it was obtained."""

    source_id: str
    wire: WireExtraction
    completion: JsonCompletion = Field(description="Raw reply, mode, cache and usage.")


class EpisodeTooLongError(ValueError):
    """An episode over ``max_episode_tokens``; episodes are never chunked."""

    def __init__(self, source_id: str, estimated: int, ceiling: int) -> None:
        super().__init__(
            f"Episode {source_id!r} is ~{estimated} tokens, over the ceiling of {ceiling}."
        )
        self.source_id = source_id
        self.estimated = estimated
        self.ceiling = ceiling


class WireShapeError(ValueError):
    """A reply that is JSON but not the flat extraction shape."""

    def __init__(self, source_id: str, error: ValidationError, content: str) -> None:
        super().__init__(
            f"Reply for {source_id!r} does not have the extraction shape: {error}"
        )
        self.source_id = source_id
        self.error = error
        self.content = content


def episode_shape(slice_: Schema) -> dict[str, Any]:
    """The JSON Schema one extraction or repair call against *slice_* is held to."""
    return instance_json_schema(slice_)


def estimate_episode_tokens(episode: Episode) -> int:
    """Estimated tokens of the text and its context."""
    chars = len(episode.text) + sum(len(item.text) for item in episode.context)
    return math.ceil(chars / CHARS_PER_TOKEN)


async def extract(
    client: LLMClient,
    slice_: Schema,
    episode: Episode,
    *,
    user_instruction: str | None = None,
    max_episode_tokens: int | None = None,
) -> Extraction:
    """Make the one extraction call for *episode* against *slice_*.

    Raises:
        EpisodeTooLongError: before any call, when the episode is over the ceiling.
        WireShapeError: the reply is a JSON object of the wrong shape.
        Any error of :meth:`LLMClient.acomplete_json`.
    """
    if max_episode_tokens is not None:
        estimated = estimate_episode_tokens(episode)
        if estimated > max_episode_tokens:
            raise EpisodeTooLongError(episode.source_id, estimated, max_episode_tokens)
    messages = build_messages(slice_, episode, user_instruction=user_instruction)
    completion = await client.acomplete_json(
        messages, episode_shape(slice_), schema_name=INSTANCE_SCHEMA_NAME
    )
    try:
        wire = WireExtraction.model_validate(completion.data)
    except ValidationError as error:
        raise WireShapeError(episode.source_id, error, completion.content) from error
    return Extraction(source_id=episode.source_id, wire=wire, completion=completion)
