"""Score a cast episode against its expected graph.

No model judges. Entities match by identity: the primary key of a natural type,
the digest of a hash type, or any complete secondary identity. A type with no
identity of its own (assigned, blank) matches by equal property values or by a
quote overlapping the expected one. Edges match by relation and matched
endpoints; an edge between the right endpoints the wrong way round counts as
reversed. Property values compare after coercion by their field, strings
ignoring case and runs of whitespace.

Every figure is a pair of integer counts, so cases pool by summing and a rate
is read off the pooled counts.
"""

from __future__ import annotations

import math
from typing import Any

from graflo.architecture.graph_types.mention import MentionGraph, Resolution
from graflo.architecture.schema.coerce import coerce_value
from graflo.architecture.schema.document import Schema
from graflo.architecture.schema.key_contract import (
    key_contract,
    key_status,
    primary_identity,
)
from graflo.architecture.schema.vertex import Vertex
from pydantic import BaseModel

from schemacast.bench.corpus import ExpectedEpisode, ExpectedGraph, ExpectedProp
from schemacast.cast import EpisodeOutcome
from schemacast.ground import Span, locate
from schemacast.llm.usage import UsageTracker
from schemacast.validate import CONSEQUENTIAL, Validation

#: Findings that are about grounding or follow from another, not about the schema.
NOT_CONFORMANCE = frozenset({"quote_not_found", "value_not_in_quote"}) | CONSEQUENTIAL


class Counts(BaseModel):
    """Additive counts for one episode, a case, a family or a run."""

    episodes: int = 0
    failed: int = 0
    requests: int = 0
    repairs: int = 0
    # The first reply, before any repair, and the attempt kept.
    emitted_first: int = 0
    nonconforming_first: int = 0
    emitted_final: int = 0
    nonconforming_final: int = 0
    ungrounded_first: int = 0
    vertices_first: int = 0
    unknown_type_first: int = 0
    edges_first: int = 0
    undeclared_edge_first: int = 0
    properties_first: int = 0
    undeclared_property_first: int = 0
    unfaithful_value_first: int = 0
    direction_fixed: int = 0
    # Against the expected graph.
    entities_expected: int = 0
    entities_produced: int = 0
    entities_matched: int = 0
    mention_agree: int = 0
    edges_expected: int = 0
    edges_produced: int = 0
    edges_matched: int = 0
    edges_reversed: int = 0
    props_expected: int = 0
    props_produced: int = 0
    props_matched: int = 0
    spans_checked: int = 0
    spans_hit: int = 0
    forbidden_expected: int = 0
    forbidden_written: int = 0
    # Selection, read apart from extraction.
    sliced: int = 0
    gold_types: int = 0
    gold_types_seeded: int = 0
    gold_types_in_slice: int = 0
    gold_edges: int = 0
    gold_edges_in_slice: int = 0
    # A second cast with a fresh model call.
    recast_vertices: int = 0
    recast_added: int = 0
    recast_minted: int = 0
    recast_duplicated: int = 0
    # Tokens, billed or replayed from the cache, and billed only.
    input_tokens: int = 0
    output_tokens: int = 0
    billed_input_tokens: int = 0
    billed_output_tokens: int = 0

    def __add__(self, other: Counts) -> Counts:
        return Counts(
            **{
                name: getattr(self, name) + getattr(other, name)
                for name in Counts.model_fields
            }
        )


def total(counts: list[Counts]) -> Counts:
    """The sum of *counts*."""
    out = Counts()
    for item in counts:
        out = out + item
    return out


def _norm(value: Any) -> Any:
    if isinstance(value, str):
        return " ".join(value.split()).casefold()
    if isinstance(value, float):
        return round(value, 9)
    if isinstance(value, list):
        return tuple(sorted((_norm(v) for v in value), key=repr))
    return value


def _same(a: Any, b: Any) -> bool:
    numbers = isinstance(a, int | float) and isinstance(b, int | float)
    if numbers and (isinstance(a, float) or isinstance(b, float)):
        return math.isclose(float(a), float(b), rel_tol=1e-9, abs_tol=1e-12)
    return _norm(a) == _norm(b)


def _hashable(value: Any) -> Any:
    return tuple(value) if isinstance(value, list) else value


def _overlap(a: Span, b: Span) -> bool:
    return a.context == b.context and a.start < b.end and b.start < a.end


def identity_keys(vertex: Vertex, props: dict[str, Any]) -> list[tuple[Any, ...]]:
    """Every key *props* complete: the primary first, then secondaries."""
    contract = key_contract(vertex)
    keys: list[tuple[Any, ...]] = []
    identity = primary_identity(vertex, contract, props)
    if identity is not None:
        keys.append(("id", *(_hashable(v) for v in identity.values())))
    for alternate in key_status(contract, props).alternates:
        keys.append((alternate.name, *(_hashable(props[f]) for f in alternate.fields)))
    return keys


class _Gold(BaseModel):
    type: str
    mention: bool
    keys: list[tuple[Any, ...]]
    props: dict[str, Any]
    span: Span | None
    prop_spans: dict[str, Span]


class _Entity(BaseModel):
    type: str
    keys: list[tuple[Any, ...]]
    mention: bool = True
    props: dict[str, Any] = {}
    spans: list[Span] = []
    prop_spans: dict[str, list[Span]] = {}


def _coerce_props(
    props: dict[str, ExpectedProp], fields: dict[str, Any], owner: str
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, prop in props.items():
        if name not in fields:
            raise ValueError(f"{owner}: no property {name!r} in the schema")
        out[name] = coerce_value(prop.value, fields[name])
    return out


def _prop_spans(
    props: dict[str, ExpectedProp], text: str, context: dict[str, str] | None = None
) -> dict[str, Span]:
    spans: dict[str, Span] = {}
    for name, prop in props.items():
        if prop.context is None:
            span = locate(prop.evidence, text)
        else:
            found = locate(prop.evidence, (context or {}).get(prop.context, ""))
            span = (
                None
                if found is None
                else found.model_copy(update={"context": prop.context})
            )
        if span is not None:
            spans[name] = span
    return spans


def gold_vertices(
    graph: ExpectedGraph,
    schema: Schema,
    text: str,
    context: dict[str, str] | None = None,
) -> dict[str, _Gold]:
    """Expected vertices with coerced properties, identity keys and spans.

    Raises:
        ValueError: a type or property the schema does not declare, or a value
            its field refuses.
    """
    config = schema.core_schema.vertex_config
    out: dict[str, _Gold] = {}
    for handle, item in graph.vertices.items():
        if item.type not in config.vertex_set:
            raise ValueError(f"{handle}: no vertex type {item.type!r}")
        vertex = config[item.type]
        fields = {f.name: f for f in vertex.properties}
        props = _coerce_props(item.props, fields, handle)
        out[handle] = _Gold(
            type=item.type,
            mention=item.mention,
            keys=identity_keys(vertex, props),
            props=props,
            span=locate(item.quote, text),
            prop_spans=_prop_spans(item.props, text, context),
        )
    return out


def _entities(
    validation: Validation, schema: Schema
) -> tuple[list[_Entity], dict[str, int]]:
    """Produced entities: refs sharing an identity are one entity."""
    config = schema.core_schema.vertex_config
    entities: list[_Entity] = []
    by_key: dict[tuple[str, tuple[Any, ...]], int] = {}
    ref_index: dict[str, int] = {}
    for valid in validation.mentions:
        values = valid.values()
        keys = identity_keys(config[valid.type], values)
        index = next(
            (by_key[(valid.type, k)] for k in keys if (valid.type, k) in by_key), None
        )
        if index is None:
            index = len(entities)
            entities.append(_Entity(type=valid.type, keys=keys))
        entity = entities[index]
        for key in keys:
            by_key.setdefault((valid.type, key), index)
            if key not in entity.keys:
                entity.keys.append(key)
        entity.mention = entity.mention and not valid.introduces
        entity.props = {**values, **entity.props}
        entity.spans.append(valid.span)
        for name, prop in valid.props.items():
            entity.prop_spans.setdefault(name, []).append(prop.span)
        ref_index[valid.ref] = index
    return entities, ref_index


def _match(
    entities: list[_Entity], gold: dict[str, _Gold], taken: set[int]
) -> dict[int, str]:
    """Produced entity index → expected handle, one to one."""
    pairs: dict[int, str] = {}
    used: set[str] = set()
    for index, entity in enumerate(entities):
        if index in taken or not entity.keys:
            continue
        for handle, item in gold.items():
            if handle in used or item.type != entity.type:
                continue
            if set(entity.keys) & set(item.keys):
                pairs[index] = handle
                used.add(handle)
                break
    candidates: list[tuple[int, int, int, str]] = []
    for index, entity in enumerate(entities):
        if index in taken or index in pairs or entity.keys:
            continue
        for handle, item in gold.items():
            if handle in used or item.type != entity.type or item.keys:
                continue
            agree = sum(
                1
                for name, value in item.props.items()
                if name in entity.props and _same(entity.props[name], value)
            )
            overlap = item.span is not None and any(
                _overlap(span, item.span) for span in entity.spans
            )
            if agree or overlap:
                candidates.append((-agree, -int(overlap), index, handle))
    for _, _, index, handle in sorted(candidates):
        if index not in pairs and handle not in used:
            pairs[index] = handle
            used.add(handle)
    return pairs


def _edge_props(
    schema: Schema, edge_id: tuple[str, str, str | None], props: dict[str, ExpectedProp]
) -> dict[str, Any]:
    edge = next(
        (e for e in schema.core_schema.edge_config.edges if e.edge_id == edge_id), None
    )
    if edge is None:
        raise ValueError(f"no edge {edge_id} in the schema")
    return _coerce_props(props, {f.name: f for f in edge.properties}, str(edge_id))


def _score_props(
    counts: Counts,
    expected: dict[str, Any],
    produced: dict[str, Any],
    gold_spans: dict[str, Span],
    spans: dict[str, list[Span]],
) -> None:
    counts.props_expected += len(expected)
    counts.props_produced += len(produced)
    for name, value in expected.items():
        if name in produced and _same(produced[name], value):
            counts.props_matched += 1
            if name in gold_spans:
                counts.spans_checked += 1
                counts.spans_hit += any(
                    _overlap(span, gold_spans[name]) for span in spans.get(name, [])
                )


def _reply(counts: Counts, validation: Validation, first: bool) -> None:
    # A finding that keeps its item (a missing key) is no conformance defect.
    findings = validation.removed
    vertices = len(validation.mentions) + sum(f.scope == "vertex" for f in findings)
    edges = len(validation.relations) + sum(f.scope == "edge" for f in findings)
    properties = (
        sum(len(m.props) for m in validation.mentions)
        + sum(len(r.props) for r in validation.relations)
        + sum(f.scope == "property" for f in findings)
    )
    nonconforming = sum(f.kind not in NOT_CONFORMANCE for f in findings)
    if not first:
        counts.emitted_final += vertices + edges + properties
        counts.nonconforming_final += nonconforming
        return
    kinds = [f.kind for f in findings]
    counts.emitted_first += vertices + edges + properties
    counts.nonconforming_first += nonconforming
    counts.ungrounded_first += kinds.count("quote_not_found")
    counts.vertices_first += vertices
    counts.unknown_type_first += kinds.count("unknown_type")
    counts.edges_first += edges
    counts.undeclared_edge_first += kinds.count("undeclared_edge")
    counts.properties_first += properties
    counts.undeclared_property_first += kinds.count("undeclared_property")
    counts.unfaithful_value_first += kinds.count("value_not_in_quote")


def _selection(
    counts: Counts,
    outcome: EpisodeOutcome,
    gold: dict[str, _Gold],
    expected: ExpectedEpisode,
) -> None:
    types = {item.type for item in gold.values()}
    edge_ids = {
        (gold[e.from_].type, gold[e.to].type, e.relation) for e in expected.edges
    }
    selection = outcome.selection
    failed = (outcome.result.failure or "").startswith("selection_")
    # A schema under budget passes whole; there is nothing to recall.
    if (selection is None and not failed) or (
        selection is not None and selection.mode != "slice"
    ):
        return
    counts.sliced += 1
    counts.gold_types += len(types)
    counts.gold_edges += len(edge_ids)
    if selection is None:
        return
    seeded: set[str] = set()
    for hit in selection.seeds:
        if hit.vertex is not None:
            seeded.add(hit.vertex)
        if hit.edge_id is not None:
            seeded.update(hit.edge_id[:2])
    in_slice = set(selection.slice.core_schema.vertex_config.vertex_set)
    slice_edges = {e.edge_id for e in selection.slice.core_schema.edge_config.edges}
    counts.gold_types_seeded += len(types & seeded)
    counts.gold_types_in_slice += len(types & in_slice)
    counts.gold_edges_in_slice += len(edge_ids & slice_edges)


def score_episode(
    outcome: EpisodeOutcome,
    expected: ExpectedEpisode,
    schema: Schema,
    text: str,
    usage: UsageTracker | None = None,
    context: dict[str, str] | None = None,
) -> Counts:
    """Counts for one cast episode; *context* maps context source ids to text."""
    counts = Counts(episodes=1)
    result = outcome.result
    counts.failed += result.status == "failed"
    counts.requests += result.requests
    counts.repairs += result.repairs
    if usage is not None:
        counts.input_tokens = (usage.billed.input_tokens or 0) + (
            usage.replayed.input_tokens or 0
        )
        counts.output_tokens = (usage.billed.output_tokens or 0) + (
            usage.replayed.output_tokens or 0
        )
        counts.billed_input_tokens = usage.billed.input_tokens or 0
        counts.billed_output_tokens = usage.billed.output_tokens or 0

    gold = gold_vertices(expected, schema, text, context)
    banned = gold_vertices(expected.forbidden, schema, text, context)
    counts.entities_expected = len(gold)
    counts.edges_expected = len(expected.edges)
    counts.forbidden_expected = len(banned) + len(expected.forbidden.edges)
    _selection(counts, outcome, gold, expected)
    edge_props = []
    for edge in expected.edges:
        if edge.from_ not in gold or edge.to not in gold:
            raise ValueError(f"edge {edge.relation}: unknown handle")
        edge_id = (gold[edge.from_].type, gold[edge.to].type, edge.relation)
        edge_props.append(_edge_props(schema, edge_id, edge.props))

    validation = outcome.validation
    if outcome.first is not None:
        _reply(counts, outcome.first, first=True)
    if validation is None:
        counts.props_expected += sum(len(g.props) for g in gold.values())
        counts.props_expected += sum(len(p) for p in edge_props)
        return counts
    _reply(counts, validation, first=False)
    counts.direction_fixed = sum(f.kind == "edge_reversed" for f in validation.fixes)

    entities, ref_index = _entities(validation, schema)
    pairs = _match(entities, gold, set())
    banned_pairs = _match(entities, banned, set(pairs))
    counts.entities_produced = len(entities)
    counts.entities_matched = len(pairs)
    for index, handle in pairs.items():
        entity, item = entities[index], gold[handle]
        counts.mention_agree += entity.mention == item.mention
        if item.span is not None:
            counts.spans_checked += 1
            counts.spans_hit += any(_overlap(s, item.span) for s in entity.spans)
        _score_props(
            counts, item.props, entity.props, item.prop_spans, entity.prop_spans
        )
    for handle, item in gold.items():
        if handle not in pairs.values():
            counts.props_expected += len(item.props)
    # A forbidden entity counts as written when resolution would write it.
    resolution = _resolution(outcome.graph, schema)
    resolved = {r.ref for r in resolution.resolved if not r.owned}
    written = {ref_index[ref] for ref in resolved if ref in ref_index}
    counts.forbidden_written += sum(index in written for index in banned_pairs)

    handles = {**pairs, **banned_pairs}
    produced: dict[
        tuple[Any, ...], tuple[dict[str, Any], list[Span], dict[str, list[Span]]]
    ] = {}
    for valid in validation.relations:
        key = (
            valid.edge_id[2],
            ref_index[valid.source],
            ref_index[valid.target],
        )
        props, spans, prop_spans = produced.setdefault(key, ({}, [], {}))
        props.update({k: v for k, v in valid.values().items() if k not in props})
        spans.append(valid.span)
        for name, prop in valid.props.items():
            prop_spans.setdefault(name, []).append(prop.span)
    counts.edges_produced = len(produced)
    by_handles = {
        (relation, handles.get(src), handles.get(tgt)): value
        for (relation, src, tgt), value in produced.items()
    }
    for edge, gold_props in zip(expected.edges, edge_props, strict=True):
        forward = by_handles.get((edge.relation, edge.from_, edge.to))
        if forward is None:
            counts.props_expected += len(gold_props)
            counts.edges_reversed += (edge.relation, edge.to, edge.from_) in by_handles
            continue
        props, spans, prop_spans = forward
        counts.edges_matched += 1
        span = locate(edge.quote, text)
        if span is not None:
            counts.spans_checked += 1
            counts.spans_hit += any(_overlap(s, span) for s in spans)
        _score_props(
            counts,
            gold_props,
            props,
            _prop_spans(edge.props, text, context),
            prop_spans,
        )
    counts.forbidden_written += sum(
        (edge.relation, edge.from_, edge.to) in by_handles
        for edge in expected.forbidden.edges
    )
    return counts


def _resolution(graph: MentionGraph | None, schema: Schema) -> Resolution:
    """*graph* resolved by its keys alone, as a cast without a database does."""
    from graflo.hq.mention_resolve import resolve_mentions

    return resolve_mentions(schema, [graph] if graph is not None else [])


def _identities(graph: MentionGraph | None, schema: Schema) -> dict[str, set[Any]]:
    from graflo.hq.mention_resolve import promote

    out: dict[str, set[Any]] = {}
    if graph is None:
        return out
    config = schema.core_schema.vertex_config
    container = promote(schema, _resolution(graph, schema))
    for name, docs in container.vertices.items():
        fields = config[name].identity
        out.setdefault(name, set()).update(
            tuple(_hashable(doc.get(f)) for f in fields) for doc in docs
        )
    return out


def score_recast(
    first: EpisodeOutcome, second: EpisodeOutcome, schema: Schema
) -> Counts:
    """What a second cast of the same episode would add to a store that upserts.

    A natural or hash identity the first cast lacked is an *added* vertex; an
    assigned or blank one is a *duplicate*, since nothing ties it to the first.
    """
    counts = Counts()
    before = _identities(first.graph, schema)
    after = _identities(second.graph, schema)
    config = schema.core_schema.vertex_config
    for name, keys in after.items():
        new = len(keys - before.get(name, set()))
        counts.recast_vertices += len(keys)
        if config[name].identity_mode in ("natural", "hash"):
            counts.recast_added += new
        else:
            counts.recast_minted += len(keys)
            counts.recast_duplicated += new
    return counts
