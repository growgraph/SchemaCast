"""Groundings: where in the text each assertion was read.

The model copies a quote; SchemaCast finds it in the episode and records the
character offsets. Offsets are never taken from the model. A quote that cannot
be found is a hallucination flag, handled by validation.

A cast's record graph carries each quote and span in graflo's ``evidence``,
as ``{"quote": str, "span": {"start": int, "end": int, "context": str}}``
(``context`` only when set); :func:`evidence`, :func:`quote_of` and
:func:`span_of` write and read it. A relation's or property's evidence also
holds its qualifiers when they are not the defaults: ``when`` (a
:class:`StatedTime`), ``modality`` (``planned`` or ``hedged``) and
``negated: true``; :func:`when_of`, :func:`modality_of` and :func:`negated_of`
read them.

A statement the text denies is grounded but is no fact: :func:`affirmed` gives
the record graph without denied relations and properties, which is what a cast
resolves and writes.
"""

from __future__ import annotations

import unicodedata
from datetime import date, datetime
from typing import Any, Literal

from graflo.architecture.graph_types.identifiers import EdgeId
from graflo.link.records import PartialRecord, RecordGraph
from pydantic import BaseModel, ConfigDict, Field

from schemacast.episode import Episode

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


class TextSpan(BaseModel):
    """Character offsets, end exclusive, into the episode's text, or into the
    text of the context item ``context`` names."""

    model_config = ConfigDict(extra="forbid")

    start: int
    end: int
    context: str | None = Field(
        default=None, description="Source id of the context item, if not the text."
    )


Span = TextSpan


class UnmappedText(BaseModel):
    """Something the text states that no type covers."""

    model_config = ConfigDict(extra="forbid")

    quote: str
    note: str
    span: TextSpan


Modality = Literal["actual", "planned", "hedged"]
Granularity = Literal["instant", "day", "week", "month", "year"]
MODALITIES: tuple[Modality, ...] = ("actual", "planned", "hedged")
GRANULARITIES: tuple[Granularity, ...] = ("instant", "day", "week", "month", "year")


class StatedTime(BaseModel):
    """When a statement says its fact holds, resolved against the episode's
    reference time."""

    model_config = ConfigDict(extra="forbid")

    quote: str = Field(description="The time phrase as it stands in the text.")
    span: TextSpan
    start: str = Field(description="ISO 8601 date or date-time.")
    end: str | None = Field(
        default=None, description="ISO 8601 date or date-time; None when open."
    )
    granularity: Granularity


def parse_iso(value: str) -> date | datetime | None:
    """*value* as an ISO 8601 date or date-time; None when it is neither."""
    try:
        return date.fromisoformat(value)
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def not_after(start: date | datetime, end: date | datetime) -> bool:
    """``start <= end``. A date compares by day with either; date-times of
    which only one carries an offset compare by wall clock."""
    if not isinstance(start, datetime) or not isinstance(end, datetime):
        first = start.date() if isinstance(start, datetime) else start
        last = end.date() if isinstance(end, datetime) else end
        return first <= last
    if (start.tzinfo is None) != (end.tzinfo is None):
        start, end = start.replace(tzinfo=None), end.replace(tzinfo=None)
    return start <= end


def evidence(
    quote: str,
    span: TextSpan,
    *,
    when: StatedTime | None = None,
    modality: Modality = "actual",
    negated: bool = False,
) -> dict[str, Any]:
    """The ``evidence`` of a record, relation or property read at *span*;
    qualifiers are set only when they differ from the defaults."""
    out: dict[str, Any] = {"quote": quote, "span": span.model_dump(exclude_none=True)}
    if when is not None:
        out["when"] = when.model_dump(mode="json", exclude_none=True)
    if modality != "actual":
        out["modality"] = modality
    if negated:
        out["negated"] = True
    return out


def quote_of(evidence: dict[str, Any] | None) -> str | None:
    """The quote an ``evidence`` holds, if any."""
    quote = (evidence or {}).get("quote")
    return quote if isinstance(quote, str) else None


def span_of(evidence: dict[str, Any] | None) -> TextSpan | None:
    """The span an ``evidence`` holds, if any."""
    span = (evidence or {}).get("span")
    return TextSpan.model_validate(span) if isinstance(span, dict) else None


def when_of(evidence: dict[str, Any] | None) -> StatedTime | None:
    """The stated time an ``evidence`` holds, if any."""
    when = (evidence or {}).get("when")
    return StatedTime.model_validate(when) if isinstance(when, dict) else None


def modality_of(evidence: dict[str, Any] | None) -> Modality:
    """The modality an ``evidence`` holds; ``actual`` when unset."""
    modality = (evidence or {}).get("modality")
    return modality if modality in ("planned", "hedged") else "actual"


def negated_of(evidence: dict[str, Any] | None) -> bool:
    """Whether an ``evidence`` marks its statement as denied."""
    return (evidence or {}).get("negated") is True


def affirmed(graph: RecordGraph) -> RecordGraph:
    """*graph* without the relations and properties its text denies, and
    without a known entity's record that only a denied relation used."""
    relations = [
        relation.model_copy(
            update={
                "props": {
                    name: prop
                    for name, prop in relation.props.items()
                    if not negated_of(prop.evidence)
                }
            }
        )
        for relation in graph.relations
        if not negated_of(relation.evidence)
    ]
    used = {ref for r in relations for ref in (r.source, r.target)}
    records: list[PartialRecord] = [
        record.model_copy(
            update={
                "props": {
                    name: prop
                    for name, prop in record.props.items()
                    if not negated_of(prop.evidence)
                }
            }
        )
        for record in graph.records
        if "known" not in (record.evidence or {}) or record.ref in used
    ]
    return graph.model_copy(update={"records": records, "relations": relations})


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

    A grounding names the record or relation of the cast's record graph it
    supports, by ref; resolution maps refs to entities. Carried in the cast
    result only; nothing here is written to the graph.
    """

    source_id: str = Field(description="Episode the assertion was read from.")
    kind: Literal["vertex", "edge", "property"]
    vertex: str | None = Field(
        default=None, description="Vertex type, for a vertex or its property."
    )
    ref: str | None = Field(
        default=None, description="Record ref, for a vertex or its property."
    )
    edge: EdgeId | None = Field(
        default=None, description="Edge, for an edge or its property."
    )
    source_ref: str | None = Field(
        default=None, description="Ref of the edge's source record."
    )
    target_ref: str | None = Field(
        default=None, description="Ref of the edge's target record."
    )
    property: str | None = Field(
        default=None, description="Property name, for a property."
    )
    char_start: int
    char_end: int
    in_context: str | None = Field(
        default=None,
        description="Source id of the context item the span points into; None "
        "for the episode's own text. Only key values ground in context.",
    )
    quote: str = Field(description="The text at the span, as it stands in the source.")
    value: Any = Field(
        default=None, description="The value stated, coerced; for a property."
    )
    when: StatedTime | None = Field(
        default=None,
        description="When the text says it holds; relations and properties.",
    )
    modality: Modality = Field(
        default="actual",
        description="``actual``, ``planned`` or ``hedged``; a vertex is ``actual``.",
    )
    negated: bool = Field(
        default=False,
        description="The text denies this relation or value; it is in no fact.",
    )
    extractor: str = Field(description="``schemacast/<version>``.")
    model: str | None = Field(
        default=None, description="Model that produced the reply."
    )
    schema_ref: str = Field(description="``<schema name>@<version>``.")
    reference_time: datetime | None = None


def groundings(
    graph: RecordGraph,
    episode: Episode,
    *,
    extractor: str,
    model: str | None = None,
) -> list[Grounding]:
    """One grounding per record, relation and stated property of *graph* whose
    ``evidence`` holds a span; none for a known entity's record. Pass the graph
    of every statement, denials included (:func:`affirmed` is what resolves)."""
    out: list[Grounding] = []

    def add(found: dict[str, Any] | None, **target: Any) -> None:
        span = span_of(found)
        if span is None:
            return
        if target["kind"] != "vertex":
            target.update(
                when=when_of(found),
                modality=modality_of(found),
                negated=negated_of(found),
            )
        text = episode.text
        if span.context is not None:
            text = [i.text for i in episode.context if i.source_id == span.context][-1]
        out.append(
            Grounding(
                source_id=episode.source_id,
                char_start=span.start,
                char_end=span.end,
                in_context=span.context,
                quote=text[span.start : span.end],
                extractor=extractor,
                model=model,
                schema_ref=graph.schema_ref,
                reference_time=episode.reference_time,
                **target,
            )
        )

    for record in graph.records:
        if "known" in (record.evidence or {}):
            continue  # stated by the source, not read from the text
        target = {"vertex": record.type, "ref": record.ref}
        add(record.evidence, kind="vertex", **target)
        for name, prop in record.props.items():
            add(
                prop.evidence,
                kind="property",
                property=name,
                value=prop.value,
                **target,
            )
    for relation in graph.relations:
        target = {
            "edge": relation.edge_id,
            "source_ref": relation.source,
            "target_ref": relation.target,
        }
        add(relation.evidence, kind="edge", **target)
        for name, prop in relation.props.items():
            add(
                prop.evidence,
                kind="property",
                property=name,
                value=prop.value,
                **target,
            )
    return out
