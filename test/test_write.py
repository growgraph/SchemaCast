"""Writing a cast through graflo: the file backend here, Neo4j on request."""

import os
from pathlib import Path

import pytest
from conftest import DATA
from graflo.connections.graflo_backend import GraFloBackendConfig
from graflo.connections.onto import Neo4jConfig
from graflo.db.manager import ConnectionManager

from schemacast.cast import SchemaCaster
from schemacast.episode import Episode
from schemacast.schema_io import load_schema
from schemacast.write import load_connection, write_cast

#: Path to a graflo connection YAML for a disposable Neo4j database. The test
#: that uses it redefines the schema there, so never point it at real data.


def _v(ref, type_, quote, props=(), mention=False):
    return {
        "ref": ref,
        "type": type_,
        "quote": quote,
        "mention": mention,
        "props": [{"name": n, "value": v, "quote": q} for n, v, q in props],
    }


def _e(relation, source, target, quote):
    return {
        "relation": relation,
        "from": source,
        "to": target,
        "quote": quote,
        "props": [],
    }


ANN = Episode(
    source_id="m1", text="Ann (ann@x.com, +1 555) operates press S-7. Note: S-7 leaks."
)
ANN_REPLY = {
    "vertices": [
        _v(
            "p",
            "Person",
            "Ann (ann@x.com, +1 555)",
            [("email", "ann@x.com", "ann@x.com"), ("phone", "+1 555", "+1 555")],
        ),
        _v("m", "Machine", "press S-7", [("serial", "S-7", "S-7")]),
        _v("n", "Note", "S-7 leaks", [("body", "S-7 leaks", "S-7 leaks")]),
        _v("o", "Machine", "S-7", [("serial", "S-7", "S-7")], mention=True),
    ],
    "edges": [
        _e("OPERATES", "p", "m", "operates press S-7"),
        _e("ABOUT", "n", "o", "S-7 leaks"),
    ],
}
CALL = Episode(
    source_id="m2",
    text="Call from +1 555: press S-7 is running again. +1 999 also uses S-7.",
)
CALL_REPLY = {
    "vertices": [
        _v("p", "Person", "+1 555", [("phone", "+1 555", "+1 555")], mention=True),
        _v("q", "Person", "+1 999", [("phone", "+1 999", "+1 999")], mention=True),
        _v("m", "Machine", "press S-7", [("serial", "S-7", "S-7")], mention=True),
    ],
    "edges": [
        _e("OPERATES", "p", "m", "+1 555: press S-7"),
        _e("OPERATES", "q", "m", "+1 999 also uses S-7"),
    ],
}


@pytest.fixture
def plant():
    return load_schema(DATA / "plant.yaml")


@pytest.fixture
def cast(make_client, plant):
    async def _cast(episodes, routes):
        client, _ = make_client(routes=routes)
        return await SchemaCaster(plant, client=client).cast(episodes)

    return _cast


def _distinct(docs, key):
    return {doc[key] for doc in docs}


async def test_writes_the_cast_to_the_file_backend(cast, tmp_path):
    result = await cast([ANN], {"m1": [ANN_REPLY]})
    conf = GraFloBackendConfig(output_dir=tmp_path)
    _, report = await write_cast(result, conf)

    assert report.schema_defined
    assert report.vertices == {"Person": 1, "Machine": 1, "Note": 1}
    with ConnectionManager(connection_config=conf) as conn:
        assert conn.fetch_all_docs("Person") == [
            {"email": "ann@x.com", "phone": "+1 555"}
        ]
        assert len(conn.fetch_all_edges("Note", "Machine", "ABOUT")) == 1
        assert len(conn.fetch_all_edges("Person", "Machine", "OPERATES")) == 1


async def test_a_second_write_reuses_the_schema_and_adds_no_identity(cast, tmp_path):
    result = await cast([ANN], {"m1": [ANN_REPLY]})
    conf = GraFloBackendConfig(output_dir=tmp_path)
    await write_cast(result, conf)
    with ConnectionManager(connection_config=conf) as conn:
        notes = _distinct(conn.fetch_all_docs("Note"), "id")

    again = await cast([ANN], {"m1": [ANN_REPLY]})
    _, report = await write_cast(again, conf)

    assert not report.schema_defined
    with ConnectionManager(connection_config=conf) as conn:
        # The file backend appends; identities are what must not grow.
        assert _distinct(conn.fetch_all_docs("Note"), "id") == notes
        assert _distinct(conn.fetch_all_docs("Person"), "email") == {"ann@x.com"}


async def test_secondary_identity_mentions_are_looked_up(cast, tmp_path):
    conf = GraFloBackendConfig(output_dir=tmp_path)
    await write_cast(await cast([ANN], {"m1": [ANN_REPLY]}), conf)

    result = await cast([CALL], {"m2": [CALL_REPLY]})
    # Without the database, neither phone number identifies anyone.
    assert [s.mention.ref for s in result.resolution.staged.mentions] == ["p", "q"]
    written, _ = await write_cast(result, conf)

    assert written.identity_of("m2", "p") == {"email": "ann@x.com"}
    assert [(s.mention.ref, s.reason) for s in written.staged.mentions] == [
        ("q", "unmatched")
    ]
    with ConnectionManager(connection_config=conf) as conn:
        edges = conn.fetch_all_edges("Person", "Machine", "OPERATES")
    assert len(edges) == 2
    assert all(edge[0] == {"email": "ann@x.com"} for edge in edges)


def test_load_connection(tmp_path):
    path = tmp_path / "conn.yaml"
    path.write_text(f"db_type: graflo_backend\noutput_dir: {tmp_path / 'db'}\n")
    conf = load_connection(path)
    assert isinstance(conf, GraFloBackendConfig)
    assert conf.output_dir == tmp_path / "db"


@pytest.mark.neo4j
async def test_neo4j_recast_adds_no_vertices(cast):
    if not os.environ.get("NEO4J_URI"):
        pytest.skip("set NEO4J_URI (and NEO4J_USERNAME, NEO4J_PASSWORD)")
    conf = Neo4jConfig()  # graflo reads NEO4J_* from the environment
    result = await cast([ANN], {"m1": [ANN_REPLY]})
    await write_cast(result, conf, recreate_schema=True)
    with ConnectionManager(connection_config=conf) as conn:
        before = {
            name: len(conn.fetch_all_docs(name))
            for name in ("Person", "Machine", "Note")
        }

    await write_cast(await cast([ANN], {"m1": [ANN_REPLY]}), conf)
    with ConnectionManager(connection_config=conf) as conn:
        after = {
            name: len(conn.fetch_all_docs(name))
            for name in ("Person", "Machine", "Note")
        }
        edges = conn.fetch_all_edges("Person", "Machine", "OPERATES")
    assert after == before == {"Person": 1, "Machine": 1, "Note": 1}
    assert len(edges) == 1


@pytest.mark.parametrize(
    ("name", "config_type"),
    [("file-backend.yaml", GraFloBackendConfig), ("neo4j.yaml", Neo4jConfig)],
)
def test_the_example_connections_load(name, config_type):
    path = Path(__file__).parents[1] / "examples" / "connections" / name
    assert isinstance(load_connection(path), config_type)
