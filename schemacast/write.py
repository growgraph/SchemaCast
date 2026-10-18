"""Write a cast to a database through graflo's ``write_container``.

The schema is defined on the target first (an existing definition is reused),
edges whose endpoint is known only by a secondary identity are looked up, and
the container is upserted by identity without an ingestion resource.

graflo's database layer is imported inside these functions: it loads drivers
and dataframe libraries that a cast without a write never needs.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from graflo.connections.onto import DBConfig
    from graflo.hq.container_write import ContainerWriteReport

    from schemacast.cast import CastResult

#: A secondary identity that matches several vertices names none of them.
LOOKUP_AMBIGUITY = "skip"


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
    result: CastResult, conn_conf: DBConfig, *, recreate_schema: bool = False
) -> ContainerWriteReport:
    """Write *result* to the database *conn_conf* points at.

    Vertices and edges are upserted by identity, so writing the same cast twice
    leaves a database that upserts unchanged. graflo's file backend appends on
    every write instead; count distinct identities there.
    """
    from graflo.hq.container_write import write_container

    return await write_container(
        result.output.graph_schema,
        result.output.data,
        conn_conf,
        pending=result.pending,
        recreate_schema=recreate_schema,
        ambiguity=LOOKUP_AMBIGUITY,
    )
