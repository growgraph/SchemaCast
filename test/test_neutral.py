"""The neutral bundle: plain JSON Lines with stable ids, joined to the text by
its groundings, readable without graflo, and only ever written on request."""

import json
import subprocess
import sys
from pathlib import Path

import jsonschema
import pytest
from click.testing import CliRunner
from conftest import DATA

from schemacast.cast import SchemaCaster
from schemacast.cli import main
from schemacast.episode import read_episodes
from schemacast.neutral import SCHEMA_FILE, save_bundle
from schemacast.schema_io import load_schema, schema_from_mapping

TICKETS = Path(__file__).parents[1] / "examples" / "tickets"

# Reads a bundle with the standard library only; run with -I -S, so no
# installed package (graflo included) can be imported.
READER = """
import json, sys
from pathlib import Path
out = Path(sys.argv[1])
rows = lambda name: [json.loads(l) for l in (out / name).read_text().splitlines()]
nodes = {n["id"]: (n["type"], n["key"]) for n in rows("nodes.jsonl")}
edges = sorted(
    ([e["relation"], nodes[e["source"]["id"]], nodes[e["target"]["id"]]]
     for e in rows("edges.jsonl")),
    key=str,
)
print(json.dumps({"nodes": sorted(nodes.values(), key=str), "edges": edges}))
"""


def _client(make_client):
    routes = json.loads((DATA / "tickets_replies.json").read_text(encoding="utf-8"))
    client, _ = make_client(
        routes={k: [json.dumps(r) for r in v] for k, v in routes.items()}
    )
    return client


@pytest.fixture
async def cast(make_client):
    schema = load_schema(TICKETS / "schema.yaml")
    episodes = list(read_episodes(TICKETS / "episodes.jsonl"))
    return await SchemaCaster(schema, client=_client(make_client)).cast(episodes)


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_every_line_has_its_declared_shape(cast, tmp_path):
    out = save_bundle(cast, tmp_path)
    shapes = json.loads(SCHEMA_FILE.read_text())
    jsonschema.Draft202012Validator.check_schema(shapes)

    def check(row, name):
        jsonschema.validate(row, {**shapes, "$ref": f"#/$defs/{name}"})

    for file, name in (
        ("nodes.jsonl", "node"),
        ("edges.jsonl", "edge"),
        ("groundings.jsonl", "grounding"),
        ("staged.jsonl", "staged"),
    ):
        for row in _rows(out / file):
            check(row, name)
    check(json.loads((out / "bundle.json").read_text()), "bundle")


def test_a_reader_without_graflo_rebuilds_the_graph(cast, tmp_path):
    out = save_bundle(cast, tmp_path)
    read = subprocess.run(
        [sys.executable, "-I", "-S", "-c", READER, str(out)],
        capture_output=True,
        text=True,
        check=True,
    )
    graph = json.loads(read.stdout)
    data = cast.output.data
    keys = {
        v.name: v.identity
        for v in cast.output.graph_schema.core_schema.vertex_config.vertices
    }

    def key(vertex_type, doc):
        return [vertex_type, {f: doc[f] for f in keys[vertex_type]}]

    assert sorted(json.dumps(n, sort_keys=True) for n in graph["nodes"]) == sorted(
        {
            json.dumps(key(t, d), sort_keys=True)
            for t, docs in data.vertices.items()
            for d in docs
        }
    )
    # One edge per id: two texts stating one relation give one edge.
    expected = {
        json.dumps([r, key(s, sd), key(t, td)], sort_keys=True)
        for (s, t, r), rows in data.edges.items()
        for sd, td, _ in rows
    }
    assert sorted(json.dumps(e, sort_keys=True) for e in graph["edges"]) == sorted(
        expected
    )


def test_groundings_join_only_what_is_in_the_bundle(cast, tmp_path):
    out = save_bundle(cast, tmp_path)
    nodes = {n["id"] for n in _rows(out / "nodes.jsonl")}
    edges = {e["id"] for e in _rows(out / "edges.jsonl")}
    groundings = _rows(out / "groundings.jsonl")
    assert all(g["node_id"] in nodes for g in groundings if "node_id" in g)
    assert all(g["edge_id"] in edges for g in groundings if "edge_id" in g)
    # A denial is grounded but is no edge.
    (denied,) = [g for g in groundings if g.get("negated") and g["kind"] == "edge"]
    assert "edge_id" not in denied
    # The customer known by email only is staged, and so is the relation to it.
    staged = [g for g in groundings if g.get("staged")]
    assert {(g["source_id"], g["kind"]) for g in staged} >= {
        ("mail-1042", "vertex"),
        ("mail-1042", "edge"),
    }
    assert not any("node_id" in g or "edge_id" in g for g in staged)
    # A relation to a known entity (k1) joins like any other.
    known = [
        g for g in groundings if g["source_id"] == "chat-2214" and g["kind"] == "edge"
    ]
    assert known and all("edge_id" in g for g in known)


def test_the_schema_travels_in_the_compact_notation_when_it_fits(cast, tmp_path):
    out = save_bundle(cast, tmp_path)
    manifest = json.loads((out / "bundle.json").read_text())
    assert manifest["schema_notation"] == "compact"
    import yaml
    from graflo.interop.compact import from_compact

    schema = from_compact(yaml.safe_load((out / "schema.yaml").read_text()))
    assert (
        schema.to_minimal_canonical_dict()
        == cast.output.graph_schema.to_minimal_canonical_dict()
    )


def test_a_schema_beyond_the_notation_travels_as_graflo_yaml():
    from schemacast.neutral import _schema_text

    schema = schema_from_mapping(
        {
            "metadata": {"name": "s"},
            "core_schema": {
                "vertex_config": {
                    "vertices": [
                        {
                            "name": "A",
                            "properties": [{"name": "k", "type": "UINT"}],
                            "identity": ["k"],
                        }
                    ]
                },
                "edge_config": {"edges": []},
            },
        }
    )
    text, notation = _schema_text(schema)
    assert notation == "graflo"
    assert "core_schema" in text


def _cast_cli(out: Path, *extra: str):
    return CliRunner().invoke(
        main,
        [
            "cast",
            "--schema",
            str(TICKETS / "schema.yaml"),
            "--episodes",
            str(TICKETS / "episodes.jsonl"),
            "--out",
            str(out),
            "--replay",
            *extra,
        ],
    )


def test_the_cli_writes_the_bundle_only_on_request(make_client, tmp_path, monkeypatch):
    import asyncio

    schema = load_schema(TICKETS / "schema.yaml")
    client = _client(make_client)
    episodes = list(read_episodes(TICKETS / "episodes.jsonl"))
    asyncio.run(SchemaCaster(schema, client=client).cast(episodes))
    monkeypatch.setenv("SCHEMACAST_LLM_MODEL", client.settings.model)
    monkeypatch.setenv("SCHEMACAST_LLM_BASE_URL", client.settings.base_url or "")

    plain = _cast_cli(tmp_path / "plain")
    assert plain.exit_code == 0, plain.output
    assert not (tmp_path / "plain" / "bundle").exists()

    first = _cast_cli(tmp_path / "a", "--bundle")
    second = _cast_cli(tmp_path / "b", "--bundle")
    assert first.exit_code == second.exit_code == 0, first.output
    # The GraFlo files are written as always, beside the bundle.
    for name in ("graph.json", "mentions.jsonl", "groundings.jsonl", "staged.jsonl"):
        assert (tmp_path / "a" / name).read_bytes() == (
            tmp_path / "plain" / name
        ).read_bytes()
    # A replay writes the same bundle, byte for byte.
    for name in (
        "nodes.jsonl",
        "edges.jsonl",
        "groundings.jsonl",
        "staged.jsonl",
        "schema.yaml",
        "bundle.json",
    ):
        assert (tmp_path / "a" / "bundle" / name).read_bytes() == (
            tmp_path / "b" / "bundle" / name
        ).read_bytes(), name


def test_one_relation_stated_twice_is_one_edge(cast, tmp_path):
    once = save_bundle(cast, tmp_path / "once")
    data = cast.output.data
    for edge_key, rows in list(data.edges.items()):
        data.edges[edge_key] = rows + rows
    twice = save_bundle(cast, tmp_path / "twice")
    assert (twice / "edges.jsonl").read_bytes() == (once / "edges.jsonl").read_bytes()
