"""Validate a reply against the slice it was asked for.

Complete on purpose: graflo's writer silently drops an edge whose key no edge
declares and a vertex with no identity value, which is right for a writer and
wrong for an extraction. Every such case is a finding here.

Deterministic fixes run first and are recorded: values are coerced to their
field type (declared units stripped), an edge given in the reverse of its
declared direction is flipped, and quotes are matched ignoring case and
whitespace runs. A property's quote is read nearest its owner's. An identity
value the text refers to but only the episode's context states is grounded
there. A number written as digits, or a date, must be what its quote states. What remains are findings, each naming the item it removes.
An item with a finding is left out; the rest of the reply stands.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any, Literal

from graflo.architecture.graph_types.identifiers import EdgeId
from graflo.architecture.schema.coerce import CoercionError, coerce_value
from graflo.architecture.schema.context import extractable_properties
from graflo.architecture.schema.document import Schema
from graflo.architecture.schema.edge import Edge
from graflo.architecture.schema.identity_digest import compute_vertex_identity
from graflo.architecture.schema.vertex import Field as SchemaField
from graflo.architecture.schema.vertex import Vertex
from pydantic import BaseModel, Field

from schemacast.episode import ContextItem
from schemacast.extract import WireExtraction, WireProp
from schemacast.fidelity import value_mismatch
from schemacast.ground import Span, locate

FindingKind = Literal[
    "duplicate_ref",
    "unknown_type",
    "undeclared_property",
    "duplicate_property",
    "coercion",
    "value_not_in_quote",
    "quote_not_found",
    "identity_insufficient",
    "dangling_ref",
    "undeclared_edge",
    "endpoint_invalid",
]
#: Order findings are listed in, in reports and repair prompts.
FINDING_ORDER: tuple[FindingKind, ...] = (
    "unknown_type",
    "duplicate_ref",
    "identity_insufficient",
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


class Fix(BaseModel):
    """A deterministic correction applied without asking the model."""

    kind: Literal["coerced", "edge_reversed", "quote_normalized", "grounded_in_context"]
    path: str
    message: str


class ValidVertex(BaseModel):
    """A vertex that passed, with coerced properties and spans."""

    ref: str
    type: str
    mention: bool
    props: dict[str, Any]
    span: Span
    prop_spans: dict[str, Span]


class ValidEdge(BaseModel):
    """An edge that passed, oriented as declared."""

    edge_id: EdgeId
    source_ref: str
    target_ref: str
    props: dict[str, Any]
    span: Span
    prop_spans: dict[str, Span]


class ValidUnmapped(BaseModel):
    """An ``unmapped`` entry, located in the text when its quote is found."""

    quote: str
    note: str
    span: Span | None


class Validation(BaseModel):
    """The checked reply."""

    vertices: list[ValidVertex] = Field(default_factory=list)
    edges: list[ValidEdge] = Field(default_factory=list)
    unmapped: list[ValidUnmapped] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    fixes: list[Fix] = Field(default_factory=list)

    @property
    def repairable(self) -> list[Finding]:
        """Findings worth showing the model: all but consequences of others."""
        return [f for f in self.findings if f.kind not in CONSEQUENTIAL]

    @property
    def kept(self) -> int:
        """Items kept: vertices, edges and properties. Used to pick the best attempt."""
        return (
            len(self.vertices)
            + len(self.edges)
            + sum(len(v.props) for v in self.vertices)
            + sum(len(e.props) for e in self.edges)
        )


def _dump(model: BaseModel) -> dict[str, Any]:
    return json.loads(model.model_dump_json(by_alias=True))


def mention_identity(
    vertex: Vertex, props: dict[str, Any]
) -> tuple[str, list[str]] | None:
    """How a mention can be looked up: ``("identity", fields)`` when the primary
    identity is complete (or its digest computable), else the first secondary
    identity whose fields are all present. None when neither."""
    mode = vertex.identity_mode
    if (
        mode == "natural"
        and vertex.identity
        and all(f in props for f in vertex.identity)
    ):
        return "identity", list(vertex.identity)
    if mode == "hash" and compute_vertex_identity(props, vertex) is not None:
        return "identity", list(vertex.identity)
    for secondary in vertex.secondary_identities:
        if all(f in props for f in secondary.fields):
            return secondary.name or "secondary", list(secondary.fields)
    return None


def identity_fields(vertex: Vertex) -> frozenset[str]:
    """Properties that take part in any identity of *vertex*."""
    names: set[str] = set()
    if vertex.identity_mode == "natural":
        names.update(vertex.identity)
    elif vertex.identity_mode == "hash":
        names.update(vertex.digest_source_fields)
    for secondary in vertex.secondary_identities:
        names.update(secondary.fields)
    return frozenset(names)


class _Checker:
    def __init__(
        self, slice_: Schema, text: str, context: Sequence[ContextItem] = ()
    ) -> None:
        self.slice = slice_
        self.text = text
        self.context = {item.source_id: item.text for item in context}
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
                    f"identity value quoted from context {source_id!r}",
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
        identity: frozenset[str] = frozenset(),
    ) -> tuple[dict[str, Any], dict[str, Span]]:
        values: dict[str, Any] = {}
        spans: dict[str, Span] = {}
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
            if prop.name in values:
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
            if span is None and prop.name in identity:
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
            values[prop.name] = value
            spans[prop.name] = span
        return values, spans

    def identity_ok(
        self, vertex: Vertex, mention: bool, props: dict[str, Any]
    ) -> str | None:
        """None when the vertex can be identified, else why not."""
        if mention:
            if mention_identity(vertex, props) is not None:
                return None
            options = [", ".join(vertex.identity)] + [
                ", ".join(s.fields) for s in vertex.secondary_identities
            ]
            if vertex.identity_mode in ("assigned", "blank"):
                return f"A {vertex.name} has no key a text can name, so it cannot be referred to."
            return f"A referred-to {vertex.name} needs all of one of: {'; '.join(options)}."
        mode = vertex.identity_mode
        if mode == "natural" and not any(f in props for f in vertex.identity):
            return (
                f"A {vertex.name} needs at least one of: {', '.join(vertex.identity)}."
            )
        if mode == "hash" and compute_vertex_identity(props, vertex) is None:
            return f"A {vertex.name} needs the properties its key is computed from: {', '.join(vertex.digest_source_fields)}."
        return None

    def vertices(self, wire: WireExtraction) -> dict[str, str]:
        """Check vertices; returns every ref the reply declared, mapped to its type."""
        vertex_config = self.slice.core_schema.vertex_config
        declared = {v.name: v for v in vertex_config.vertices}
        refs: dict[str, str] = {}
        for i, wv in enumerate(wire.vertices):
            path = f"vertices[{i}]"
            common = {
                "scope": "vertex",
                "path": path,
                "ref": wv.ref,
                "fragment": _dump(wv),
            }
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
            props, prop_spans = self.props(
                wv.props,
                fields,
                wv.type,
                path,
                wv.ref,
                near=span,
                identity=identity_fields(vertex),
            )
            if span is None:
                self.find(
                    kind="quote_not_found",
                    message=f"The quote {wv.quote!r} is not in the text.",
                    **common,
                )
                continue
            reason = self.identity_ok(vertex, wv.mention, props)
            if reason is not None:
                self.find(kind="identity_insufficient", message=reason, **common)
                continue
            self.result.vertices.append(
                ValidVertex(
                    ref=wv.ref,
                    type=wv.type,
                    mention=wv.mention,
                    props=props,
                    span=span,
                    prop_spans=prop_spans,
                )
            )
        return refs

    def declared_edge(self, edge_id: EdgeId) -> Edge | None:
        edge = self.slice.core_schema.edge_config.declared(edge_id)
        return edge if edge is not None and edge.edge_id == edge_id else None

    def edges(self, wire: WireExtraction, refs: dict[str, str]) -> None:
        valid_refs = {v.ref for v in self.result.vertices}
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
            props, prop_spans = self.props(
                we.props, fields, f"edge {edge.relation or ''}", path, None, near=span
            )
            if span is None:
                self.find(
                    kind="quote_not_found",
                    message=f"The quote {we.quote!r} is not in the text.",
                    **common,
                )
                continue
            self.result.edges.append(
                ValidEdge(
                    edge_id=edge.edge_id,
                    source_ref=source_ref,
                    target_ref=target_ref,
                    props=props,
                    span=span,
                    prop_spans=prop_spans,
                )
            )

    def unmapped(self, wire: WireExtraction) -> None:
        for item in wire.unmapped:
            self.result.unmapped.append(
                ValidUnmapped(
                    quote=item.quote, note=item.note, span=locate(item.quote, self.text)
                )
            )


def validate(
    wire: WireExtraction,
    slice_: Schema,
    text: str,
    context: Sequence[ContextItem] = (),
) -> Validation:
    """Check *wire* against *slice_*, the episode *text* and its *context*."""
    checker = _Checker(slice_, text, context)
    refs = checker.vertices(wire)
    checker.edges(wire, refs)
    checker.unmapped(wire)
    order = {kind: i for i, kind in enumerate(FINDING_ORDER)}
    checker.result.findings.sort(key=lambda f: order[f.kind])
    return checker.result
