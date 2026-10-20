"""Load the governed schema a cast runs against."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml
from graflo.architecture.schema.document import Schema


def schema_from_mapping(data: Any, source: str = "schema") -> Schema:
    """A GraFlo ``Schema`` from a mapping: a schema itself (``core_schema`` at the
    top) or a ``GraphManifest`` (``schema`` or ``graph_schema``).

    Raises:
        TypeError: when *data* is not a mapping.
        ValueError: when the mapping is neither a schema nor a manifest.
    """
    if not isinstance(data, dict):
        raise TypeError(f"{source}: expected a mapping, got {type(data).__name__}")
    if "core_schema" in data:
        return Schema.model_validate(data)
    if "schema" in data or "graph_schema" in data:
        # Deferred: the manifest contract pulls in the ingestion stack.
        from graflo.architecture.contract.manifest import GraphManifest

        return GraphManifest.from_config(data).require_schema()
    raise ValueError(
        f"{source}: neither a schema (no 'core_schema') nor a manifest (no 'schema')"
    )


def load_schema(path: str | Path) -> Schema:
    """Read a GraFlo ``Schema``, or the schema block of a ``GraphManifest``.

    YAML or JSON; see :func:`schema_from_mapping`.

    Raises:
        TypeError: when the file does not hold a mapping.
        ValueError: when the mapping is neither a schema nor a manifest.
    """
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    data: Any = (
        json.loads(text) if path.suffix.lower() == ".json" else yaml.safe_load(text)
    )
    return schema_from_mapping(data, str(path))


def schema_ref(schema: Schema) -> str:
    """``<name>@<version>`` of a schema."""
    return f"{schema.metadata.name}@{schema.metadata.version or ''}"


def load_contract(path: str | Path) -> tuple[Schema, frozenset[str]]:
    """The schema of *path* and its connector-owned vertex types.

    A manifest names which types its connectors ingest; text links to those and
    never creates them. A schema file owns nothing.
    """
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    data: Any = (
        json.loads(text) if path.suffix.lower() == ".json" else yaml.safe_load(text)
    )
    schema = schema_from_mapping(data, str(path))
    if isinstance(data, dict) and "core_schema" not in data:
        from graflo.architecture.contract.manifest import GraphManifest
        from graflo.architecture.contract.ownership import connector_owned_types

        return schema, connector_owned_types(GraphManifest.from_config(data))
    return schema, frozenset()
