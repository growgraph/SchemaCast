"""Write a cast to a database through graflo's ``write_mention_graphs``.

The schema is defined on the target first (an existing definition is reused),
the cast's mention graphs are resolved again with the database to look keys up
in, and what they identify is upserted by identity without an ingestion
resource. What is still not identified is returned staged, not written.

graflo's database layer is imported inside these functions: it loads drivers
and dataframe libraries that a cast without a write never needs.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from graflo.architecture.graph_types.mention import Resolution
    from graflo.connections.onto import DBConfig
    from graflo.hq.container_write import ContainerWriteReport

    from schemacast.cast import CastResult


def __getattr__(name: str) -> Any:
    # ``WriteReport`` is graflo's ``ContainerWriteReport``, loaded on first use.
    if name == "WriteReport":
        from graflo.hq.container_write import ContainerWriteReport

        return ContainerWriteReport
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def load_connection(path: str | Path) -> DBConfig:
    """A graflo connection config from a YAML file (``db_type`` plus its fields)."""
    from graflo.connections.onto import DBConfig

    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TypeError(f"{path}: expected a mapping")
    return DBConfig.from_dict(data)


async def write_cast(
    result: CastResult,
    conn_conf: DBConfig,
    *,
    owned: frozenset[str] = frozenset(),
    recreate_schema: bool = False,
) -> tuple[Resolution, ContainerWriteReport]:
    """Resolve *result*'s mention graphs against *conn_conf* and write them.

    *owned* types are linked to what the database holds, never created. Writing
    upserts by identity, so writing the same cast twice leaves a database that
    upserts unchanged. graflo's file backend appends on every write instead;
    count distinct identities there.
    """
    from graflo.hq.mention_resolve import write_mention_graphs

    return await write_mention_graphs(
        result.output.graph_schema,
        result.graphs,
        conn_conf,
        owned=owned,
        recreate_schema=recreate_schema,
    )
