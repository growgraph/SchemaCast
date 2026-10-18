"""Groundings: where in the text each assertion was read.

The model copies a quote; SchemaCast finds it in the episode and records the
character offsets. Offsets are never taken from the model. A quote that cannot
be found is a hallucination flag, handled by validation.
"""

from __future__ import annotations

import unicodedata
from datetime import datetime
from typing import Any, Literal

from graflo.architecture.graph_types.identifiers import EdgeId
from pydantic import BaseModel, Field

_EDGE_PUNCTUATION = " \t\r\n.,;:!?\"'`()[]{}«»“”‘’"


#: Typographic quotes and dashes, read as their plain forms.
_TYPOGRAPHIC = str.maketrans(
    {
        "“": '"',
        "”": '"',
        "„": '"',
        "‟": '"',
        "«": '"',
        "»": '"',
        "‘": "'",
        "’": "'",
        "‚": "'",
        "‛": "'",
        "′": "'",
        "‐": "-",
        "‑": "-",
        "‒": "-",
        "–": "-",
        "—": "-",
        "―": "-",
        "−": "-",
    }
)


class Span(BaseModel):
    """Character offsets, end exclusive, into the episode's text or, when
    ``context`` is set, into the text of that context item."""

    start: int
    end: int
    context: str | None = Field(
        default=None, description="Source id of the context item, if not the text."
    )


def _fold(char: str) -> str:
    decomposed = unicodedata.normalize("NFKD", char.translate(_TYPOGRAPHIC))
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


def _normalized(text: str) -> tuple[str, list[int]]:
    """Whitespace runs collapsed to one space, case and accents folded, with
    each output character's index in *text*."""
    out: list[str] = []
    index: list[int] = []
    in_space = False
    for position, char in enumerate(text):
        if char.isspace():
            if not in_space:
                out.append(" ")
                index.append(position)
            in_space = True
            continue
        in_space = False
        for folded in _fold(char):
            out.append(folded)
            index.append(position)
    return "".join(out), index


def _all(needle: str, haystack: str) -> list[int]:
    starts: list[int] = []
    found = haystack.find(needle)
    while found != -1:
        starts.append(found)
        found = haystack.find(needle, found + 1)
    return starts


def _gap(span: Span, near: Span) -> int:
    return max(0, near.start - span.end, span.start - near.end)


def _occurrences(quote: str, text: str) -> list[Span]:
    exact = _all(quote, text)
    if exact:
        return [Span(start=s, end=s + len(quote)) for s in exact]
    haystack, index = _normalized(text)
    for candidate in (quote, quote.strip(_EDGE_PUNCTUATION)):
        needle = _normalized(candidate.strip())[0]
        if not needle:
            continue
        found = _all(needle, haystack)
        if found:
            return [
                Span(start=index[s], end=index[s + len(needle) - 1] + 1) for s in found
            ]
    return []


def locate(quote: str, text: str, *, near: Span | None = None) -> Span | None:
    """Find *quote* in *text*: exactly, then ignoring case, accents, whitespace
    runs and typographic quotes and dashes, then also without surrounding
    punctuation. Of several occurrences, the one nearest *near* wins, else the
    first."""
    if not quote.strip():
        return None
    spans = _occurrences(quote, text)
    if not spans:
        return None
    if near is None:
        return spans[0]
    return min(spans, key=lambda span: (_gap(span, near), span.start))


class Grounding(BaseModel):
    """One assertion and the span of text it was read from.

    Carried in the cast result only; nothing here is written to the graph.
    """

    source_id: str = Field(description="Episode the assertion was read from.")
    kind: Literal["vertex", "edge", "property"]
    vertex: str | None = Field(
        default=None, description="Vertex type, for a vertex or its property."
    )
    identity: dict[str, Any] | None = Field(
        default=None, description="Identity of the vertex, as written or looked up by."
    )
    edge: EdgeId | None = Field(
        default=None, description="Edge, for an edge or its property."
    )
    source_identity: dict[str, Any] | None = None
    target_identity: dict[str, Any] | None = None
    property: str | None = Field(
        default=None, description="Property name, for a property."
    )
    char_start: int
    char_end: int
    in_context: str | None = Field(
        default=None,
        description="Source id of the context item the span points into; None "
        "for the episode's own text. Only identity values ground in context.",
    )
    quote: str = Field(description="The text at the span, as it stands in the source.")
    extractor: str = Field(description="``schemacast/<version>``.")
    model: str | None = Field(
        default=None, description="Model that produced the reply."
    )
    schema_ref: str = Field(description="``<schema name>@<version>``.")
    reference_time: datetime | None = None
