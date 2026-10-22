"""Score a cast episode against its expected graph.

No model judges. Entities match by identity: the primary key of a natural type,
the digest of a hash type, or any complete secondary identity. A type with no
identity of its own (assigned, blank) matches by equal property values or by a
quote overlapping the expected one. Edges match by relation and matched
endpoints; an edge between the right endpoints the wrong way round counts as
reversed. Property values compare after coercion by their field, strings
ignoring case and runs of whitespace. An expected edge may end at a known
entity by its ref (``k1``…); it matches by ref, and known entities are not
counted as entities.

Every figure is a pair of integer counts, so cases pool by summing and a rate
is read off the pooled counts.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

from graflo.architecture.schema.coerce import coerce_value
from graflo.architecture.schema.document import Schema
from graflo.architecture.schema.key_contract import (
    key_contract,
    key_status,
    primary_identity,
)
from graflo.architecture.schema.vertex import Vertex
from graflo.link.records import RecordGraph, Resolution
from pydantic import BaseModel

from schemacast.bench.corpus import (
    ExpectedEpisode,
    ExpectedGraph,
    ExpectedPicks,
    ExpectedProp,
)
from schemacast.cast import EpisodeOutcome
from schemacast.episode import KnownEntity
from schemacast.ground import Span, locate, quote_of, span_of
from schemacast.llm.usage import UsageTracker
from schemacast.pick import Pick
from schemacast.prompt import known_refs
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
    # Pick, for the staged mentions a case gives candidates for.
    picks_expected: int = 0
    picks_offered: int = 0
    picks_correct: int = 0
    picks_linked: int = 0
    picks_wrong: int = 0
    picks_rejected: int = 0
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


def _spans(evidence: dict[str, Any] | None) -> list[Span]:
    span = span_of(evidence)
    return [] if span is None else [span]


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
    quotes: list[str] = []
    prop_spans: dict[str, list[Span]] = {}


class Misses(BaseModel):
    """What one episode's cast and its expected graph do not share, by quote.

    Expected items no produced item matched, and produced items that matched
    no expected or allowed item (a forbidden match is listed and marked).
    """

    expected_entities: list[str] = []
    expected_edges: list[str] = []
    produced_entities: list[str] = []
    produced_edges: list[str] = []

    @property
    def empty(self) -> bool:
        return not (
            self.expected_entities
            or self.expected_edges
            or self.produced_entities
            or self.produced_edges
        )


def _named(kind: str | None, quote: str | None) -> str:
    return f"{kind or '?'} {_quoted(quote)}"


def _quoted(quote: str | None) -> str:
    return "(no quote)" if quote is None else '"' + " ".join(quote.split()) + '"'


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
        entity.mention = entity.mention and not valid.may_create
        entity.props = {**values, **entity.props}
        entity.spans.extend(_spans(valid.evidence))
        quote = quote_of(valid.evidence)
        if quote is not None:
            entity.quotes.append(quote)
        for name, prop in valid.props.items():
            entity.prop_spans.setdefault(name, []).extend(_spans(prop.evidence))
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
    kinds: dict[str, str],
) -> None:
    types = {item.type for item in gold.values()}
    edge_ids = {(kinds[e.from_], kinds[e.to], e.relation) for e in expected.edges}
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
    seeded: set[str] = set(selection.pinned)
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
    misses: Misses | None = None,
    known: Sequence[KnownEntity] = (),
) -> Counts:
    """Counts for one cast episode; *context* maps context source ids to text.

    *misses*, when given, is filled with what the cast and the expected graph
    do not share. *known* are the episode's known entities, whose refs expected
    edges may name.
    """
    misses = misses if misses is not None else Misses()
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
    allowed = gold_vertices(expected.allowed, schema, text, context)
    counts.entities_expected = len(gold)
    counts.edges_expected = len(expected.edges)
    counts.forbidden_expected = len(banned) + len(expected.forbidden.edges)
    kinds = {
        ref: entity.vertex_type for ref, entity in known_refs(known, schema).items()
    }
    kinds.update({handle: item.type for handle, item in gold.items()})
    _selection(counts, outcome, gold, expected, kinds)
    edge_props = []
    for edge in expected.edges:
        if edge.from_ not in kinds or edge.to not in kinds:
            raise ValueError(f"edge {edge.relation}: unknown handle")
        edge_id = (kinds[edge.from_], kinds[edge.to], edge.relation)
        edge_props.append(_edge_props(schema, edge_id, edge.props))

    def gold_quote(handle: str) -> str:
        item = expected.vertices[handle]
        return _named(item.type, item.quote)

    def missed(pairs_: dict[int, str], matched_edges: set[int]) -> None:
        misses.expected_entities += [
            gold_quote(h) for h in expected.vertices if h not in pairs_.values()
        ]
        misses.expected_edges += [
            f"{_named(edge.relation, edge.quote)} ({edge.from_} -> {edge.to})"
            for i, edge in enumerate(expected.edges)
            if i not in matched_edges
        ]

    validation = outcome.validation
    if outcome.first is not None:
        _reply(counts, outcome.first, first=True)
    if validation is None:
        counts.props_expected += sum(len(g.props) for g in gold.values())
        counts.props_expected += sum(len(p) for p in edge_props)
        missed({}, set())
        return counts
    _reply(counts, validation, first=False)
    counts.direction_fixed = sum(f.kind == "edge_reversed" for f in validation.fixes)

    entities, ref_index = _entities(validation, schema)
    pairs = _match(entities, gold, set())
    banned_pairs = _match(entities, banned, set(pairs))
    allowed_pairs = _match(entities, allowed, set(pairs) | set(banned_pairs))
    counts.entities_produced = len(entities) - len(allowed_pairs)
    counts.entities_matched = len(pairs)
    for index, entity in enumerate(entities):
        if index in pairs or index in allowed_pairs:
            continue
        line = _named(entity.type, entity.quotes[0] if entity.quotes else None)
        misses.produced_entities.append(
            line + (" (forbidden)" if index in banned_pairs else "")
        )
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

    handles: dict[int, str] = {**pairs, **banned_pairs, **allowed_pairs}
    known_used = {record.ref for record in validation.known}

    def node(ref: str) -> int | str:
        # A known entity is its own handle.
        return ref if ref in known_used else ref_index[ref]

    def handle(item: int | str) -> str | None:
        return item if isinstance(item, str) else handles.get(item)

    produced: dict[
        tuple[Any, ...], tuple[dict[str, Any], list[Span], dict[str, list[Span]]]
    ] = {}
    edge_quotes: dict[tuple[Any, ...], str | None] = {}
    for valid in validation.relations:
        key = (valid.edge_id[2], node(valid.source), node(valid.target))
        props, spans, prop_spans = produced.setdefault(key, ({}, [], {}))
        edge_quotes.setdefault(key, quote_of(valid.evidence))
        props.update({k: v for k, v in valid.values().items() if k not in props})
        spans.extend(_spans(valid.evidence))
        for name, prop in valid.props.items():
            prop_spans.setdefault(name, []).extend(_spans(prop.evidence))
    by_handles = {
        (relation, handle(src), handle(tgt)): value
        for (relation, src, tgt), value in produced.items()
    }
    allowed_edges = {
        (edge.relation, edge.from_, edge.to) for edge in expected.allowed.edges
    }
    taken = {
        (edge.relation, edge.from_, edge.to) for edge in expected.edges
    } | allowed_edges
    counts.edges_produced = len(produced) - sum(
        (relation, handle(src), handle(tgt)) in allowed_edges
        for relation, src, tgt in produced
    )
    for key in produced:
        relation, src, tgt = key
        if (relation, handle(src), handle(tgt)) not in taken:
            misses.produced_edges.append(_named(relation, edge_quotes[key]))
    matched_edges: set[int] = set()
    for number, (edge, gold_props) in enumerate(
        zip(expected.edges, edge_props, strict=True)
    ):
        forward = by_handles.get((edge.relation, edge.from_, edge.to))
        if forward is None:
            counts.props_expected += len(gold_props)
            counts.edges_reversed += (edge.relation, edge.to, edge.from_) in by_handles
            continue
        matched_edges.add(number)
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
    missed(pairs, matched_edges)
    return counts


def _resolution(graph: RecordGraph | None, schema: Schema) -> Resolution:
    """*graph* resolved by its keys alone, as a cast without a database does."""
    from graflo.link.resolve import resolve_records

    return resolve_records(schema, [graph] if graph is not None else [])


def _identities(graph: RecordGraph | None, schema: Schema) -> dict[str, set[Any]]:
    from graflo.link.resolve import promote

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


def ref_handles(
    outcome: EpisodeOutcome,
    expected: ExpectedEpisode,
    schema: Schema,
    text: str,
    context: dict[str, str] | None = None,
) -> dict[str, str]:
    """Produced mention ref → the expected vertex handle it matched."""
    if outcome.validation is None:
        return {}
    gold = gold_vertices(expected, schema, text, context)
    entities, ref_index = _entities(outcome.validation, schema)
    pairs = _match(entities, gold, set())
    return {ref: pairs[index] for ref, index in ref_index.items() if index in pairs}


def score_picks(picks: list[Pick], refs: dict[str, str], spec: ExpectedPicks) -> Counts:
    """Pick counts: a pick is correct when what it links is the expected entity,
    or nothing when none fits. A mention the cast did not stage counts as missed."""
    counts = Counts(picks_expected=len(spec.mentions))
    for pick in picks:
        handle = refs.get(pick.ref)
        if handle not in spec.mentions:
            continue
        wanted = spec.mentions[handle].pick
        counts.picks_offered += pick.outcome != "no_candidates"
        counts.picks_rejected += pick.outcome == "rejected"
        linked = pick.key if pick.outcome == "picked" else None
        counts.picks_linked += linked is not None
        same = (linked is None and wanted is None) or (
            linked is not None
            and wanted is not None
            and {k: str(v) for k, v in linked.items()}
            == {k: str(v) for k, v in wanted.items()}
        )
        counts.picks_correct += same
        counts.picks_wrong += linked is not None and not same
    return counts
