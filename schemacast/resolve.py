"""Resolve identities and assemble the graph container of one episode.

Identities are computed before edges are built, because graflo's writer fills
digest and minted keys on vertex documents only, never on the endpoint
documents inside edges.

| Identity mode | Key |
|---|---|
| natural | the identity properties the text stated |
| hash / funnel | the digest of its source properties |
| assigned, blank | a UUID derived from the schema, the episode, the type and the ref |

Derived UUIDs make a replay byte-identical; a fresh model call that names its
refs differently still mints new vertices for assigned and blank types.

A mention (``mention: true``) is never written as a vertex. Edges reach it by
its primary identity, or — when the text gave only a secondary identity — are
held as *pending* until the write looks the primary identity up.

A vertex the model picked from the episode's known entities takes that
entity's key, whatever its identity mode: it is the vertex the graph holds.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from graflo.architecture.graph_types.container import GraphContainer
from graflo.architecture.graph_types.identifiers import EdgeId
from graflo.architecture.graph_types.pending import EdgeEndpoint, PendingEdge
from graflo.architecture.schema.document import Schema
from graflo.architecture.schema.identity_digest import compute_vertex_identity
from graflo.architecture.schema.vertex import Vertex
from pydantic import BaseModel, ConfigDict, Field

from schemacast.episode import Episode
from schemacast.ground import Grounding, Span
from schemacast.validate import Validation, ValidVertex, mention_identity

#: Namespace of SchemaCast's derived UUIDs. Never change it: every assigned and
#: blank key ever minted depends on it.
MINT_NAMESPACE = uuid.UUID("6f1c3a52-6a51-4f8e-9e0e-2b8c1d7a4e10")


def schema_ref(schema: Schema) -> str:
    """``<name>@<version>`` of a schema."""
    return f"{schema.metadata.name}@{schema.metadata.version or ''}"


def mint_id(schema: Schema, source_id: str, vertex_type: str, ref: str) -> str:
    """The UUID of an assigned or blank vertex the model introduced as *ref*."""
    name = "\x1f".join((schema_ref(schema), source_id, vertex_type, ref))
    return str(uuid.uuid5(MINT_NAMESPACE, name))


#: How an edge reaches one vertex: graflo's ``EdgeEndpoint``.
Endpoint = EdgeEndpoint


class Resolution(BaseModel):
    """One episode's graph, ready to write."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    container: GraphContainer
    pending: list[PendingEdge] = Field(default_factory=list)
    groundings: list[Grounding] = Field(default_factory=list)


ResolvedBy = Literal["identity", "secondary", "candidate", "minted"]


def _resolved_by(vertex: Vertex, valid: ValidVertex) -> ResolvedBy:
    if valid.candidate_key is not None:
        return "candidate"
    if valid.mention:
        found = mention_identity(vertex, valid.props)
        return (
            "identity" if found is not None and found[0] == "identity" else "secondary"
        )
    return "identity" if vertex.identity_mode in ("natural", "hash") else "minted"


def _endpoint(
    vertex: Vertex, valid: ValidVertex, slice_: Schema, source_id: str
) -> Endpoint:
    mode = vertex.identity_mode
    key = vertex.identity[0] if vertex.identity else "id"
    if valid.candidate_key is not None:
        # A picked entity is written under the key the graph already holds.
        return Endpoint(vertex=vertex.name, identity=dict(valid.candidate_key))
    if valid.mention:
        found = mention_identity(vertex, valid.props)
        assert found is not None, "validation admits only identifiable mentions"
        selector, fields = found
        if selector == "identity":
            if mode == "hash":
                return Endpoint(
                    vertex=vertex.name,
                    identity={key: compute_vertex_identity(valid.props, vertex)},
                )
            return Endpoint(
                vertex=vertex.name, identity={f: valid.props[f] for f in fields}
            )
        return Endpoint(vertex=vertex.name, match={f: valid.props[f] for f in fields})
    if mode == "natural":
        return Endpoint(
            vertex=vertex.name,
            identity={f: valid.props[f] for f in vertex.identity if f in valid.props},
        )
    if mode == "hash":
        return Endpoint(
            vertex=vertex.name,
            identity={key: compute_vertex_identity(valid.props, vertex)},
        )
    return Endpoint(
        vertex=vertex.name,
        identity={key: mint_id(slice_, source_id, vertex.name, valid.ref)},
    )


def _grounding(
    episode: Episode,
    span: Span,
    *,
    extractor: str,
    model: str | None,
    ref: str,
    **target: Any,
) -> Grounding:
    text = episode.text
    if span.context is not None:
        text = [i.text for i in episode.context if i.source_id == span.context][-1]
    return Grounding(
        source_id=episode.source_id,
        char_start=span.start,
        char_end=span.end,
        in_context=span.context,
        quote=text[span.start : span.end],
        extractor=extractor,
        model=model,
        schema_ref=ref,
        reference_time=episode.reference_time,
        **target,
    )


def resolve(
    validation: Validation,
    slice_: Schema,
    episode: Episode,
    *,
    extractor: str,
    model: str | None = None,
) -> Resolution:
    """Build the episode's container, pending edges and groundings."""
    vertex_config = slice_.core_schema.vertex_config
    ref = schema_ref(slice_)
    vertices: dict[str, list[dict[str, Any]]] = {}
    edges: dict[
        EdgeId, list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]]
    ] = {}
    pending: list[PendingEdge] = []
    groundings: list[Grounding] = []
    endpoints: dict[str, Endpoint] = {}

    for valid in validation.vertices:
        vertex = vertex_config[valid.type]
        endpoint = _endpoint(vertex, valid, slice_, episode.source_id)
        endpoints[valid.ref] = endpoint
        if not valid.mention:
            assert endpoint.identity is not None
            vertices.setdefault(valid.type, []).append(
                {**valid.props, **endpoint.identity}
            )
        target = {"vertex": valid.type, "identity": endpoint.identity or endpoint.match}
        groundings.append(
            _grounding(
                episode,
                valid.span,
                extractor=extractor,
                model=model,
                ref=ref,
                kind="vertex",
                resolved_by=_resolved_by(vertex, valid),
                lane=valid.lane,
                **target,
            )
        )
        for name, span in valid.prop_spans.items():
            groundings.append(
                _grounding(
                    episode,
                    span,
                    extractor=extractor,
                    model=model,
                    ref=ref,
                    kind="property",
                    property=name,
                    **target,
                )
            )

    for valid in validation.edges:
        source = endpoints[valid.source_ref]
        target = endpoints[valid.target_ref]
        if source.identity is not None and target.identity is not None:
            edges.setdefault(valid.edge_id, []).append(
                (dict(source.identity), dict(target.identity), dict(valid.props))
            )
        else:
            pending.append(
                PendingEdge(
                    edge_id=valid.edge_id,
                    source=source,
                    target=target,
                    props=valid.props,
                )
            )
        edge_target = {
            "edge": valid.edge_id,
            "source_identity": source.identity or source.match,
            "target_identity": target.identity or target.match,
        }
        groundings.append(
            _grounding(
                episode,
                valid.span,
                extractor=extractor,
                model=model,
                ref=ref,
                kind="edge",
                **edge_target,
            )
        )
        for name, span in valid.prop_spans.items():
            groundings.append(
                _grounding(
                    episode,
                    span,
                    extractor=extractor,
                    model=model,
                    ref=ref,
                    kind="property",
                    property=name,
                    **edge_target,
                )
            )

    return Resolution(
        container=GraphContainer(vertices=vertices, edges=edges),
        pending=pending,
        groundings=groundings,
    )


def merge_containers(containers: list[GraphContainer]) -> GraphContainer:
    """One container holding every vertex and edge of *containers*, in order."""
    vertices: dict[str, list[Any]] = {}
    edges: dict[EdgeId, list[Any]] = {}
    for container in containers:
        for name, docs in container.vertices.items():
            vertices.setdefault(name, []).extend(docs)
        for edge_id, triples in container.edges.items():
            edges.setdefault(edge_id, []).extend(triples)
    return GraphContainer(vertices=vertices, edges=edges)
