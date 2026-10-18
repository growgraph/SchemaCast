"""SchemaCast: text to a labeled property graph, against a governed GraFlo schema.

The names below load on first use, so ``import schemacast`` stays light.
"""

from __future__ import annotations

import importlib
from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING, Any

try:
    __version__ = version("schemacast")
except PackageNotFoundError:  # running from a source tree without install
    __version__ = "0.0.0"

if TYPE_CHECKING:
    from schemacast.cast import CastResult, EpisodeResult, SchemaCaster
    from schemacast.config import CacheSettings, LLMSettings, SchemaCastSettings
    from schemacast.episode import Episode, read_episodes
    from schemacast.ground import Grounding
    from schemacast.output import save_cast
    from schemacast.schema_io import load_schema
    from schemacast.sink import JsonlQuarantineSink, MemoryQuarantineSink
    from schemacast.write import WriteReport, load_connection, write_cast

_EXPORTS: dict[str, str] = {
    "CacheSettings": "schemacast.config",
    "CastResult": "schemacast.cast",
    "Episode": "schemacast.episode",
    "EpisodeResult": "schemacast.cast",
    "Grounding": "schemacast.ground",
    "JsonlQuarantineSink": "schemacast.sink",
    "LLMSettings": "schemacast.config",
    "SchemaCastSettings": "schemacast.config",
    "SchemaCaster": "schemacast.cast",
    "MemoryQuarantineSink": "schemacast.sink",
    "WriteReport": "schemacast.write",
    "load_connection": "schemacast.write",
    "load_schema": "schemacast.schema_io",
    "read_episodes": "schemacast.episode",
    "save_cast": "schemacast.output",
    "write_cast": "schemacast.write",
}

__all__ = [
    "CacheSettings",
    "CastResult",
    "Episode",
    "EpisodeResult",
    "Grounding",
    "JsonlQuarantineSink",
    "LLMSettings",
    "MemoryQuarantineSink",
    "SchemaCastSettings",
    "SchemaCaster",
    "WriteReport",
    "__version__",
    "load_connection",
    "load_schema",
    "read_episodes",
    "save_cast",
    "write_cast",
]


def __getattr__(name: str) -> Any:
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module 'schemacast' has no attribute {name!r}")
    value = getattr(importlib.import_module(module), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(__all__)
