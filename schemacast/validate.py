"""Validate a reply against the slice it was asked for.

Complete on purpose: graflo's writer silently drops an edge whose key no edge
declares and a vertex with no identity value, which is right for a writer and
wrong for an extraction. Every such case is a finding here.

Deterministic fixes run first and are recorded: values are coerced to their
field type (declared units stripped), an edge given in the reverse of its
declared direction is flipped, and quotes are matched ignoring case and
whitespace runs. A property's quote is read nearest its owner's. An identity
value the text refers to but only the episode's context states is grounded
there. A number written as digits, or a date, must be what its quote states.
What remains are findings, each naming the item it removes; an item with a
finding is left out, and the rest of the reply stands.

Validation decides no identity. A mention that introduces an entity but states
no complete key gets one non-removing finding, so a repair can ask for a key
the reply left out; the mention is kept either way, and resolution decides.
A record that states two complete keys must find them in one sentence: a key
a sentence apart from the first is removed (``keys_apart``), so one record
never claims two entities are one.

Kept mentions and relations are graflo records (``PartialRecord``,
``RecordRelation``); each one's quote and span, and each property's, are its
``evidence`` (:func:`schemacast.ground.evidence`).

An episode's known entities are refs ``k1``… (:func:`schemacast.prompt.known_refs`)
an edge may end at. A vertex the reply gives such a ref is a ``known_ref``
finding. Each known entity a kept relation uses becomes a record in
:attr:`Validation.known`, stating its key from the source: its ``evidence`` is
``{"known": role, "label": label}``, with no quote or span.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any, Literal

from graflo.architecture.graph_types.identifiers import EdgeId
from graflo.architecture.schema.coerce import CoercionError, coerce_value
from graflo.architecture.schema.context import extractable_properties
from graflo.architecture.schema.document import Schema
from graflo.architecture.schema.edge import Edge
from graflo.architecture.schema.key_contract import (
    KeyContract,
    KeySet,
    key_contract,
    key_status,
)
from graflo.architecture.schema.vertex import Field as SchemaField
from graflo.link.records import (
    PartialRecord,
    RecordGraph,
    RecordRelation,
    StatedProp,
)
from pydantic import BaseModel, Field

from schemacast.episode import ContextItem, KnownEntity
from schemacast.extract import WireExtraction, WireProp
from schemacast.fidelity import value_mismatch
from schemacast.ground import Span, UnmappedText, evidence, locate, span_of
from schemacast.prompt import known_refs

FindingKind = Literal[
    "known_ref",
    "duplicate_ref",
    "unknown_type",
    "undeclared_property",
    "duplicate_property",
    "coercion",
    "value_not_in_quote",
    "quote_not_found",
    "identity_insufficient",
    "keys_apart",
    "dangling_ref",
    "undeclared_edge",
    "endpoint_invalid",
]
#: Order findings are listed in, in reports and repair prompts.
FINDING_ORDER: tuple[FindingKind, ...] = (
    "unknown_type",
    "known_ref",
    "duplicate_ref",
    "identity_insufficient",
    "keys_apart",
    "undeclared_edge",
    "dangling_ref",
    "undeclared_property",
    "duplicate_property",
    "coercion",
    "value_not_in_quote",
    "quote_not_found",
    "endpoint_invalid",
)
#: Findings that follow from another and go away when it is fixed.
CONSEQUENTIAL: frozenset[FindingKind] = frozenset({"endpoint_invalid"})

Scope = Literal["vertex", "edge", "property"]


class Finding(BaseModel):
    """A defect in a reply, and the item it removes."""

    kind: FindingKind
    scope: Scope = Field(
        description="What is left out: a vertex, an edge or one property."
    )
    path: str = Field(
        description="Location in the reply, e.g. ``vertices[1].props[0]``."
    )
    ref: str | None = Field(default=None, description="Vertex ref involved, if any.")
    detail: str | None = Field(
        default=None, description="Sub-kind, e.g. the coercion kind."
    )
    message: str
    fragment: dict[str, Any] = Field(
        description="The offending item as the model wrote it."
    )
    removes: bool = Field(
        default=True,
        description="False when the item is kept and the finding only asks a repair.",
    )


class Fix(BaseModel):
    """A deterministic correction applied without asking the model."""

    kind: Literal[
        "coerced",
        "edge_reversed",
        "quote_normalized",
        "grounded_in_context",
    ]
    path: str
    message: str


class Validation(BaseModel):
    """The checked reply."""

    mentions: list[PartialRecord] = Field(default_factory=list)
    relations: list[RecordRelation] = Field(default_factory=list)
    known: list[PartialRecord] = Field(
        default_factory=list,
        description="Known entities a kept relation uses (refs ``k1``…): their "
        "key from the source, never created, never grounded.",
    )
    unmapped: list[UnmappedText] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    fixes: list[Fix] = Field(default_factory=list)

    @property
    def repairable(self) -> list[Finding]:
        """Findings worth showing the model: all but consequences of others."""
        return [f for f in self.findings if f.kind not in CONSEQUENTIAL]

    @property
    def removed(self) -> list[Finding]:
        """Findings that left an item out."""
        return [f for f in self.findings if f.removes]

    @property
    def kept(self) -> int:
        """Items kept: mentions, relations and properties. Used to pick the best attempt."""
        return (
            len(self.mentions)
            + len(self.relations)
            + sum(len(m.props) for m in self.mentions)
            + sum(len(r.props) for r in self.relations)
        )

    def graph(
        self, source_id: str, schema_ref: str, reference_time: Any = None
    ) -> RecordGraph:
        """The record graph this reply states; ``unmapped`` stays here."""
        return RecordGraph(
            source_id=source_id,
            schema_ref=schema_ref,
            reference_time=reference_time,
            records=[*self.mentions, *self.known],
            relations=self.relations,
        )


#: Where one sentence ends and the next starts: a full stop, question or
#: exclamation mark before whitespace, or a blank line.
_SENTENCE_END = re.compile(r"[.!?]\s|\n\s*\n")


def keys_apart(
    contract: KeyContract, props: dict[str, StatedProp], text: str
) -> list[str]:
    """Key fields a record states a sentence apart from its first key.

    Two complete keys on one record say they name one entity, and a text says
    that within one sentence ("Juniper (2026.10)"). Keys a sentence apart most
    often belong to two entities the reply merged ("firmware 3.0. The fix ships
    in Juniper"), however wide its quote. The first complete key (primary
    before alternate) stays; a key grounded in the episode's context is a
    reference and never counts.
    """

    def reach(key: KeySet) -> tuple[int, int] | None:
        spans = [span_of(props[field].evidence) for field in key.fields]
        if any(span is None or span.context is not None for span in spans):
            return None
        return min(s.start for s in spans if s), max(s.end for s in spans if s)

    placed = [
        (key, at)
        for key in (*contract.keys, *contract.alternates)
        if all(field in props for field in key.fields)
        and (at := reach(key)) is not None
    ]
    if len(placed) < 2:
        return []
    first, (start, end) = placed[0]

    def together(at: tuple[int, int]) -> bool:
        between = text[min(end, at[1]) : max(start, at[0])]
        return _SENTENCE_END.search(between) is None

    kept = set(first.fields)
    apart = {f for key, at in placed[1:] if not together(at) for f in key.fields}
    return sorted(apart - kept)


def _dump(model: BaseModel) -> dict[str, Any]:
    return json.loads(model.model_dump_json(by_alias=True))


class _Checker:
    def __init__(
        self,
        slice_: Schema,
        text: str,
        context: Sequence[ContextItem] = (),
        known: Sequence[KnownEntity] = (),
    ) -> None:
        self.slice = slice_
        self.text = text
        self.context = {item.source_id: item.text for item in context}
        self.known = known_refs(known, slice_)
        self.result = Validation()

    def source(self, span: Span) -> str:
        text = self.text if span.context is None else self.context[span.context]
        return text[span.start : span.end]

    def find(self, **kwargs: Any) -> None:
        self.result.findings.append(Finding(**kwargs))

    def fix(self, kind: Any, path: str, message: str) -> None:
        self.result.fixes.append(Fix(kind=kind, path=path, message=message))

    def span(self, quote: str, path: str, *, near: Span | None = None) -> Span | None:
        span = locate(quote, self.text, near=near)
        if span is not None and self.text[span.start : span.end] != quote:
            self.fix(
                "quote_normalized",
                path,
                f"quote matched as {self.text[span.start : span.end]!r}",
            )
        return span

    def context_span(self, quote: str, path: str) -> Span | None:
        """*quote* in the context, latest item first."""
        for source_id, text in reversed(self.context.items()):
            span = locate(quote, text)
            if span is not None:
                self.fix(
                    "grounded_in_context",
                    path,
                    f"key value quoted from context {source_id!r}",
                )
                return span.model_copy(update={"context": source_id})
        return None

    def props(
        self,
        wire_props: list[WireProp],
        fields: dict[str, SchemaField],
        owner: str,
        path: str,
        ref: str | None,
        *,
        near: Span | None = None,
        keys: frozenset[str] = frozenset(),
    ) -> dict[str, StatedProp]:
        stated: dict[str, StatedProp] = {}
        for j, prop in enumerate(wire_props):
            prop_path = f"{path}.props[{j}]"
            fragment = _dump(prop)
            common = {
                "scope": "property",
                "path": prop_path,
                "ref": ref,
                "fragment": fragment,
            }
            field = fields.get(prop.name)
            if field is None:
                self.find(
                    kind="undeclared_property",
                    message=f"{owner} has no property {prop.name!r}; it has: {', '.join(fields) or 'none'}.",
                    **common,
                )
                continue
            if prop.name in stated:
                self.find(
                    kind="duplicate_property",
                    message=f"{prop.name!r} is given more than once for {owner}.",
                    **common,
                )
                continue
            try:
                value = coerce_value(prop.value, field)
            except CoercionError as error:
                self.find(
                    kind="coercion", detail=error.kind, message=str(error), **common
                )
                continue
            span = self.span(prop.quote, prop_path, near=near)
            if span is None and prop.name in keys:
                span = self.context_span(prop.quote, prop_path)
            if span is None:
                self.find(
                    kind="quote_not_found",
                    message=f"The quote {prop.quote!r} for {prop.name!r} is not in the text.",
                    **common,
                )
                continue
            mismatch = value_mismatch(value, field, self.source(span))
            if mismatch is not None:
                self.find(kind="value_not_in_quote", message=mismatch, **common)
                continue
            if value != prop.value:
                self.fix("coerced", prop_path, f"{prop.value!r} -> {value!r}")
            stated[prop.name] = StatedProp(
                value=value, evidence=evidence(prop.quote, span)
            )
        return stated

    def key_gap(
        self, vertex_type: str, introduces: bool, values: dict[str, Any]
    ) -> str | None:
        """Why an introduced entity cannot be identified, or None."""
        contract = key_contract(self.slice.core_schema.vertex_config[vertex_type])
        if not introduces or contract.source == "minted":
            return None
        status = key_status(contract, values)
        if status.state not in ("partial", "none"):
            return None
        message = f"A {vertex_type} is identified by {', '.join(status.missing)}"
        if contract.alternates:
            alternates = "; ".join(", ".join(a.fields) for a in contract.alternates)
            message += f", or found by {alternates}"
        return message + "."

    def vertices(self, wire: WireExtraction) -> dict[str, str]:
        """Check vertices; returns every ref the reply declared, mapped to its type."""
        vertex_config = self.slice.core_schema.vertex_config
        declared = {v.name: v for v in vertex_config.vertices}
        refs = {ref: entity.vertex_type for ref, entity in self.known.items()}
        for i, wv in enumerate(wire.vertices):
            path = f"vertices[{i}]"
            common = {
                "scope": "vertex",
                "path": path,
                "ref": wv.ref,
                "fragment": _dump(wv),
            }
            if wv.ref in self.known:
                self.find(
                    kind="known_ref",
                    message=f"{wv.ref!r} is a KNOWN entity, already in the graph.",
                    **common,
                )
                continue
            if wv.ref in refs:
                self.find(
                    kind="duplicate_ref",
                    message=f"Ref {wv.ref!r} is used twice.",
                    **common,
                )
                continue
            refs[wv.ref] = wv.type
            vertex = declared.get(wv.type)
            if vertex is None:
                self.find(
                    kind="unknown_type",
                    message=f"Type {wv.type!r} is not in TYPES.",
                    **common,
                )
                continue
            fields = {f.name: f for f in extractable_properties(vertex)}
            span = self.span(wv.quote, path)
            props = self.props(
                wv.props,
                fields,
                wv.type,
                path,
                wv.ref,
                near=span,
                keys=key_contract(vertex).key_fields,
            )
            if span is None:
                self.find(
                    kind="quote_not_found",
                    message=f"The quote {wv.quote!r} is not in the text.",
                    **common,
                )
                continue
            for name in keys_apart(key_contract(vertex), props, self.text):
                j = next(j for j, p in enumerate(wv.props) if p.name == name)
                quote = props.pop(name).evidence or {}
                self.find(
                    kind="keys_apart",
                    scope="property",
                    path=f"{path}.props[{j}]",
                    ref=wv.ref,
                    fragment=_dump(wv.props[j]),
                    message=(
                        f"{wv.ref!r} states {name!r} from {quote.get('quote')!r}, a "
                        "sentence apart from its other key."
                    ),
                )
            introduces = not wv.mention
            values = {name: prop.value for name, prop in props.items()}
            gap = self.key_gap(wv.type, introduces, values)
            if gap is not None:
                self.find(
                    kind="identity_insufficient", message=gap, removes=False, **common
                )
            self.result.mentions.append(
                PartialRecord(
                    ref=wv.ref,
                    type=wv.type,
                    may_create=introduces,
                    props=props,
                    evidence=evidence(wv.quote, span),
                )
            )
        return refs

    def declared_edge(self, edge_id: EdgeId) -> Edge | None:
        edge = self.slice.core_schema.edge_config.declared(edge_id)
        return edge if edge is not None and edge.edge_id == edge_id else None

    def edges(self, wire: WireExtraction, refs: dict[str, str]) -> None:
        valid_refs = {m.ref for m in self.result.mentions} | set(self.known)
        for k, we in enumerate(wire.edges):
            path = f"edges[{k}]"
            common = {"scope": "edge", "path": path, "fragment": _dump(we)}
            missing = [r for r in (we.from_, we.to) if r not in refs]
            if missing:
                self.find(
                    kind="dangling_ref",
                    ref=missing[0],
                    message=f"No vertex has ref {missing[0]!r}.",
                    **common,
                )
                continue
            invalid = [r for r in (we.from_, we.to) if r not in valid_refs]
            if invalid:
                self.find(
                    kind="endpoint_invalid",
                    ref=invalid[0],
                    message=f"Vertex {invalid[0]!r} was left out, so this edge is too.",
                    **common,
                )
                continue
            source_ref, target_ref = we.from_, we.to
            edge_id: EdgeId = (refs[source_ref], refs[target_ref], we.relation)
            edge = self.declared_edge(edge_id)
            if edge is None:
                reversed_id: EdgeId = (edge_id[1], edge_id[0], edge_id[2])
                edge = self.declared_edge(reversed_id)
                if edge is not None:
                    source_ref, target_ref = target_ref, source_ref
                    self.fix(
                        "edge_reversed",
                        path,
                        f"{edge_id} given; declared as {reversed_id}",
                    )
            if edge is None:
                self.find(
                    kind="undeclared_edge",
                    message=(
                        f"No edge ({edge_id[0]})-[{edge_id[2] or ''}]->({edge_id[1]}) "
                        "is declared in TYPES."
                    ),
                    **common,
                )
                continue
            fields = {f.name: f for f in edge.properties}
            span = self.span(we.quote, path)
            props = self.props(
                we.props, fields, f"edge {edge.relation or ''}", path, None, near=span
            )
            if span is None:
                self.find(
                    kind="quote_not_found",
                    message=f"The quote {we.quote!r} is not in the text.",
                    **common,
                )
                continue
            self.result.relations.append(
                RecordRelation(
                    edge_id=edge.edge_id,
                    source=source_ref,
                    target=target_ref,
                    props=props,
                    evidence=evidence(we.quote, span),
                )
            )

    def known_records(self) -> None:
        used = {r for rel in self.result.relations for r in (rel.source, rel.target)}
        for ref, entity in self.known.items():
            if ref not in used:
                continue
            mark = {"known": entity.role}
            self.result.known.append(
                PartialRecord(
                    ref=ref,
                    type=entity.vertex_type,
                    may_create=False,
                    props={
                        name: StatedProp(value=value, evidence=mark)
                        for name, value in entity.key.items()
                    },
                    evidence={**mark, "label": entity.label},
                )
            )

    def unmapped(self, wire: WireExtraction) -> None:
        for item in wire.unmapped:
            # A quote not found in the text is about the prompt around it.
            span = locate(item.quote, self.text)
            if span is not None:
                self.result.unmapped.append(
                    UnmappedText(quote=item.quote, note=item.note, span=span)
                )


def validate(
    wire: WireExtraction,
    slice_: Schema,
    text: str,
    context: Sequence[ContextItem] = (),
    known: Sequence[KnownEntity] = (),
) -> Validation:
    """Check *wire* against *slice_*, the episode *text*, its *context* and its
    *known* entities."""
    checker = _Checker(slice_, text, context, known)
    refs = checker.vertices(wire)
    checker.edges(wire, refs)
    checker.known_records()
    checker.unmapped(wire)
    order = {kind: i for i, kind in enumerate(FINDING_ORDER)}
    checker.result.findings.sort(key=lambda f: order[f.kind])
    return checker.result
