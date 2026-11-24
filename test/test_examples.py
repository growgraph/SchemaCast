"""The shipped examples stay valid: the schema loads, the episodes parse,
selection runs on every text without a model, and the pick input is what the
cast stages."""

import json
from pathlib import Path

from schemacast.episode import read_episodes
from schemacast.schema_io import load_schema
from schemacast.select import select

EXAMPLES = Path(__file__).parents[1] / "examples"


def test_the_tickets_example_selects_every_episode():
    schema = load_schema(EXAMPLES / "tickets" / "schema.yaml")
    episodes = list(read_episodes(EXAMPLES / "tickets" / "episodes.jsonl"))
    assert len(episodes) == 7
    for episode in episodes:
        assert select(schema, episode.text).slice.core_schema.vertex_config.vertices


async def _cast_tickets(make_client):
    # Replies a model gave for these texts, so the cast runs without one.
    from conftest import DATA

    from schemacast.cast import SchemaCaster

    routes = json.loads((DATA / "tickets_replies.json").read_text(encoding="utf-8"))
    client, _ = make_client(
        routes={k: [json.dumps(r) for r in v] for k, v in routes.items()}
    )
    schema = load_schema(EXAMPLES / "tickets" / "schema.yaml")
    episodes = list(read_episodes(EXAMPLES / "tickets" / "episodes.jsonl"))
    return await SchemaCaster(schema, client=client).cast(episodes)


async def test_the_tickets_example_casts_into_mentions_and_resolves(make_client):
    result = await _cast_tickets(make_client)

    vertices = result.output.data.vertices
    # A ticket the text only refers to keeps what the text states about it.
    tickets = {doc["number"]: doc for doc in vertices["Ticket"]}
    assert tickets["HD-4411"]["wait"] == 26.0
    assert tickets["HD-4411"]["severity"] == 2
    assert set(tickets) == {"HD-4411", "HD-4415"}
    # Only mail-1043 states the Desk Fan, by name and SKU, so it creates it.
    assert {doc["sku"] for doc in vertices["Product"]} == {"SKU-77", "SKU-81"}
    assert result.output.data.edges[("Ticket", "Ticket", "DUPLICATE_OF")] == [
        ({"number": "HD-4415"}, {"number": "HD-4411"}, {})
    ]
    # What the texts name without a key waits, with its reason: the customer
    # known by email only, and "the customer" and "replacement dimmer" of a note.
    staged = result.resolution.staged
    assert [(s.source_id, s.record.type, s.reason) for s in staged.records] == [
        ("mail-1042", "Customer", "unmatched"),
        ("note-88", "Customer", "no_key"),
        ("note-88", "Product", "no_key"),
    ]
    assert [h.relation.edge_id for h in staged.relations] == [
        ("Customer", "Ticket", "RAISED")
    ]
    # The injected instruction writes nothing.
    assert next(e for e in result.episodes if e.source_id == "chat-2210").mentions == 0


async def test_the_tickets_example_relates_to_a_known_ticket(make_client):
    result = await _cast_tickets(make_client)
    episode = next(e for e in result.episodes if e.source_id == "chat-2214")
    assert episode.known == 1
    edges = result.output.data.edges
    assert ({"handle": "kim.r"}, {"number": "HD-4411"}, {}) in edges[
        ("Agent", "Ticket", "HANDLES")
    ]
    tagged = edges[("Ticket", "Tag", "TAGGED")]
    assert [source for source, _, _ in tagged] == [{"number": "HD-4411"}] * 2


async def test_the_tickets_example_keeps_qualifiers_in_groundings(make_client):
    result = await _cast_tickets(make_client)
    edges = [
        g for g in result.groundings if g.source_id == "mail-1043" and g.kind == "edge"
    ]
    denied = next(g for g in edges if g.negated)
    planned = next(g for g in edges if g.modality == "planned")
    assert denied.quote == "not the Desk Lamp (SKU-77)"
    assert planned.when is not None and planned.when.start == "2026-03-09"
    # A denial is no fact: HD-4415 is about the fan only.
    about = result.output.data.edges[("Ticket", "Product", "ABOUT")]
    assert [t for s, t, _ in about if s == {"number": "HD-4415"}] == [{"sku": "SKU-81"}]


async def test_the_tickets_pick_input_is_what_the_cast_stages(make_client):
    from schemacast.cli import read_pick_items

    result = await _cast_tickets(make_client)
    staged = next(
        s for s in result.resolution.staged.records if s.source_id == "mail-1042"
    )
    (item,) = read_pick_items(EXAMPLES / "tickets" / "pick.jsonl")
    assert item.staged[0].record == staged.record
    assert [c.key for c in item.staged[0].candidates] == [
        {"account": "ACC-2291"},
        {"account": "ACC-3307"},
    ]


def test_the_hello_demo_replays_with_no_key_and_no_request(tmp_path):
    """examples/hello ships real replies in cache/. A change to the prompt, the
    reply schema or the hello schema misses them: record again with
    ``rm -rf examples/hello/cache &&
    uv run python examples/hello/demo.py --record > examples/hello/expected.txt``
    (a paid call per note)."""
    import os
    import subprocess
    import sys

    import jsonschema
    import pytest

    from schemacast.neutral import SCHEMA_FILE

    hello = EXAMPLES / "hello"
    if not any(p.is_file() for p in (hello / "cache").rglob("*")):
        pytest.skip("examples/hello/cache is not recorded yet: demo.py --record")
    env = {k: v for k, v in os.environ.items() if not k.startswith("SCHEMACAST_")}
    run = subprocess.run(
        [sys.executable, str(hello / "demo.py")],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        check=False,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    # The docs show this printout.
    assert run.stdout == (hello / "expected.txt").read_text(encoding="utf-8")
    for episode in read_episodes(hello / "episodes.jsonl"):
        assert episode.source_id in run.stdout
    out = tmp_path / "out" / "hello"
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["usage"]["calls"] == 0
    assert manifest["status"]["failed"] == 0
    shapes = json.loads(SCHEMA_FILE.read_text())
    bundle = json.loads((out / "bundle" / "bundle.json").read_text())
    jsonschema.validate(bundle, {**shapes, "$ref": "#/$defs/bundle"})
    assert bundle["schema_notation"] == "compact"


def test_the_hello_example_replays_from_the_command_line(tmp_path, monkeypatch):
    """The README's command: ``schemacast cast --replay`` on the compact schema,
    served from the recorded replies."""
    import pytest
    from click.testing import CliRunner

    from schemacast.cli import main

    hello = EXAMPLES / "hello"
    if not any(p.is_file() for p in (hello / "cache").rglob("*")):
        pytest.skip("examples/hello/cache is not recorded yet: demo.py --record")
    import os

    for name in [k for k in os.environ if k.startswith("SCHEMACAST_")]:
        monkeypatch.delenv(name)
    monkeypatch.setenv("SCHEMACAST_CACHE_DIR", str(hello / "cache"))
    run = CliRunner().invoke(
        main,
        [
            "cast",
            "--replay",
            "--schema",
            str(hello / "schema.yaml"),
            "--episodes",
            str(hello / "episodes.jsonl"),
            "--out",
            str(tmp_path),
        ],
    )
    assert run.exit_code == 0, run.output
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["usage"]["calls"] == 0
    assert manifest["status"]["failed"] == 0
