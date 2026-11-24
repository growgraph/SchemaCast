"""The neutral bundle: a cast as plain JSON Lines, readable without graflo.

An optional output beside the GraFlo files ``save_cast`` writes, never instead
of them. Every node and edge has a stable id, and every grounding names the
node or edge it supports, so text and graph join from the files alone.

| File | Holds |
|---|---|
| ``nodes.jsonl`` | ``{id, type, key, properties}`` |
| ``edges.jsonl`` | ``{id, relation, source: {type, id}, target: {type, id}, properties}`` |
| ``groundings.jsonl`` | each grounding, with ``node_id`` or ``edge_id`` when that item is in the bundle |
| ``staged.jsonl`` | ``{source_id, ref, type, stated, reason}`` per mention not identified |
| ``schema.yaml`` | the schema: compact notation when it fits, else GraFlo's canonical form |
| ``bundle.json`` | format version, producers, schema reference, notation and counts |

A node id is a digest of its type and key; an edge id a digest of its relation,
its endpoints' ids and the values of the edge's identity fields. A grounding
gets ``node_id`` or ``edge_id`` only when that id is in the bundle: a denied
relation, a staged mention and a relation waiting on one get none, and a
staged ref is marked ``staged``. The shapes are in ``neutral.schema.json``
beside this module.
"""

from __future__ import annotations

import hashlib
import json
from importlib.metadata import version
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from pydantic_core import to_jsonable_python

from schemacast import __version__
from schemacast.schema_io import schema_ref

if TYPE_CHECKING:
    from graflo.architecture.schema.document import Schema
    from graflo.link.records import Resolution

    from schemacast.cast import CastResult

BUNDLE_FORMAT_VERSION = 1
BUNDLE_DIR = "bundle"
SCHEMA_FILE = Path(__file__).with_name("neutral.schema.json")


def _digest(payload: dict[str, Any]) -> str:
    text = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def node_id(vertex_type: str, key: dict[str, Any]) -> str:
    """The id of the node of *vertex_type* with *key* (its identity fields)."""
    return _digest({"type": vertex_type, "key": to_jsonable_python(key)})


def edge_id(
    relation: str | None, source: str, target: str, identity: dict[str, Any]
) -> str:
    """The id of an edge: its relation, its endpoints' node ids and the values
    of its identity fields (empty for an edge identified by its endpoints)."""
    return _digest(
        {
            "relation": relation,
            "source": source,
            "target": target,
            "identity": to_jsonable_python(identity),
        }
    )


def _line(row: dict[str, Any]) -> str:
    return json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"


class _Index:
    """Identity fields of every vertex type and edge of a schema."""

    def __init__(self, schema: Schema) -> None:
        core = schema.core_schema
        self.vertex_key = {
            v.name: list(v.identity) for v in core.vertex_config.vertices
        }
        self.edge_key = {
            (e.source, e.target, e.relation): sorted(
                {f for i in e.identities for f in i}
            )
            for e in core.edge_config.edges
        }

    def node(self, vertex_type: str, doc: dict[str, Any]) -> tuple[str, dict, dict]:
        fields = self.vertex_key[vertex_type]
        key = {f: doc[f] for f in fields}
        rest = {f: v for f, v in doc.items() if f not in fields}
        return node_id(vertex_type, key), key, rest


def _schema_text(schema: Schema) -> tuple[str, str]:
    from graflo.interop.compact import CompactUnsupported, to_compact

    try:
        data, notation = to_compact(schema), "compact"
    except CompactUnsupported:
        data, notation = schema.to_minimal_canonical_dict(), "graflo"
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True), notation


def save_bundle(
    result: CastResult,
    out_dir: str | Path,
    *,
    resolution: Resolution | None = None,
) -> Path:
    """Write the neutral bundle of *result* under *out_dir*; returns it.

    *resolution*, from a database write, replaces the offline one, as for
    ``save_cast``.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    resolution = resolution or result.resolution
    schema = result.output.graph_schema
    index = _Index(schema)
    data = result.output.data

    # The container keeps one row per statement; the bundle keeps one per id,
    # each later statement's properties updating the first.
    nodes_by_id: dict[str, dict[str, Any]] = {}
    for vertex_type, docs in data.vertices.items():
        for doc in docs:
            nid, key, rest = index.node(vertex_type, to_jsonable_python(doc))
            node = nodes_by_id.setdefault(
                nid, {"id": nid, "type": vertex_type, "key": key, "properties": {}}
            )
            node["properties"].update(rest)
    nodes = list(nodes_by_id.values())
    node_ids = set(nodes_by_id)

    edges_by_id: dict[str, dict[str, Any]] = {}
    for (source_type, target_type, relation), rows in data.edges.items():
        identity_fields = index.edge_key.get((source_type, target_type, relation), [])
        for source_doc, target_doc, props in rows:
            source, _, _ = index.node(source_type, to_jsonable_python(source_doc))
            target, _, _ = index.node(target_type, to_jsonable_python(target_doc))
            props = to_jsonable_python(props)
            identity = {f: props.get(f) for f in identity_fields}
            eid = edge_id(relation, source, target, identity)
            edge = edges_by_id.setdefault(
                eid,
                {
                    "id": eid,
                    "relation": relation,
                    "source": {"type": source_type, "id": source},
                    "target": {"type": target_type, "id": target},
                    "properties": {},
                },
            )
            edge["properties"].update(props)
    edges = list(edges_by_id.values())
    # An edge with identity fields is joined to a grounding only when one edge matches.
    by_ends: dict[tuple[Any, ...], list[str]] = {}
    for edge in edges:
        ends = (edge["relation"], edge["source"]["id"], edge["target"]["id"])
        by_ends.setdefault(ends, []).append(edge["id"])

    resolved = {
        (r.source_id, r.ref): node_id(
            r.type, {f: r.identity.get(f) for f in index.vertex_key[r.type]}
        )
        for r in resolution.resolved
        if r.type in index.vertex_key
    }
    staged_refs = {(s.source_id, s.record.ref) for s in resolution.staged.records}

    groundings: list[dict[str, Any]] = []
    for grounding in result.groundings:
        row = grounding.model_dump(mode="json", exclude_none=True)
        if grounding.edge is not None:
            source = resolved.get((grounding.source_id, grounding.source_ref or ""))
            target = resolved.get((grounding.source_id, grounding.target_ref or ""))
            ids = by_ends.get((grounding.edge[2], source, target), [])
            if not grounding.negated and len(ids) == 1:
                row["edge_id"] = ids[0]
            ends = {
                (grounding.source_id, r)
                for r in (grounding.source_ref, grounding.target_ref)
            }
            if ends & staged_refs:
                row["staged"] = True
        elif grounding.ref is not None:
            nid = resolved.get((grounding.source_id, grounding.ref))
            if nid in node_ids and not grounding.negated:
                row["node_id"] = nid
            if (grounding.source_id, grounding.ref) in staged_refs:
                row["staged"] = True
        groundings.append(row)

    staged = [
        {
            "source_id": s.source_id,
            "ref": s.record.ref,
            "type": s.record.type,
            "stated": {
                name: to_jsonable_python(prop.value)
                for name, prop in (s.record.props or {}).items()
            },
            "reason": s.reason,
        }
        for s in resolution.staged.records
    ]

    nodes.sort(key=lambda n: (n["type"], n["id"]))
    edges.sort(key=lambda e: (str(e["relation"]), e["id"]))
    schema_text, notation = _schema_text(schema)
    files = {
        "nodes.jsonl": "".join(_line(n) for n in nodes),
        "edges.jsonl": "".join(_line(e) for e in edges),
        "groundings.jsonl": "".join(_line(g) for g in groundings),
        "staged.jsonl": "".join(_line(s) for s in staged),
        "schema.yaml": schema_text,
    }
    manifest = {
        "bundle_format_version": BUNDLE_FORMAT_VERSION,
        "producer": {"schemacast": __version__, "graflo": version("graflo")},
        "schema_ref": schema_ref(schema),
        "schema_notation": notation,
        "counts": {
            "nodes": len(nodes),
            "edges": len(edges),
            "groundings": len(groundings),
            "staged": len(staged),
        },
        "files": sorted(files),
    }
    files["bundle.json"] = (
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=1) + "\n"
    )
    for name, text in files.items():
        (out / name).write_text(text, encoding="utf-8")
    return out
