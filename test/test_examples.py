"""The shipped examples stay valid: the schema loads, the episodes parse, and
selection runs on every text without a model."""

import json
from pathlib import Path

from schemacast.episode import read_episodes
from schemacast.schema_io import load_schema
from schemacast.select import select

EXAMPLES = Path(__file__).parents[1] / "examples"


def test_the_tickets_example_selects_every_episode():
    schema = load_schema(EXAMPLES / "tickets" / "schema.yaml")
    episodes = list(read_episodes(EXAMPLES / "tickets" / "episodes.jsonl"))
    assert len(episodes) == 5
    for episode in episodes:
        assert select(schema, episode.text).slice.core_schema.vertex_config.vertices


async def test_the_tickets_example_casts_into_mentions_and_resolves(make_client):
    # Replies a model gave for these texts, so the cast runs without one.
    from conftest import DATA

    from schemacast.cast import SchemaCaster

    routes = json.loads((DATA / "tickets_replies.json").read_text(encoding="utf-8"))
    client, _ = make_client(
        routes={k: [json.dumps(r) for r in v] for k, v in routes.items()}
    )
    schema = load_schema(EXAMPLES / "tickets" / "schema.yaml")
    episodes = list(read_episodes(EXAMPLES / "tickets" / "episodes.jsonl"))
    result = await SchemaCaster(schema, client=client).cast(episodes)

    vertices = result.output.data.vertices
    # A ticket the text only refers to keeps what the text states about it.
    tickets = {doc["number"]: doc for doc in vertices["Ticket"]}
    assert tickets["HD-4411"]["wait"] == 26.0
    assert tickets["HD-4411"]["severity"] == 2
    assert set(tickets) == {"HD-4411", "HD-4415"}
    assert result.output.data.edges[("Ticket", "Ticket", "DUPLICATE_OF")] == [
        ({"number": "HD-4415"}, {"number": "HD-4411"}, {})
    ]
    # What the texts name without a key waits, with its reason: the customer
    # known by email only, and "the customer" and "replacement dimmer" of a note.
    staged = result.resolution.staged
    assert [(s.source_id, s.mention.type, s.reason) for s in staged.mentions] == [
        ("mail-1042", "Customer", "unmatched"),
        ("note-88", "Customer", "no_key"),
        ("note-88", "Product", "no_key"),
    ]
    assert [h.relation.edge_id for h in staged.relations] == [
        ("Customer", "Ticket", "RAISED")
    ]
    # The injected instruction writes nothing.
    assert next(e for e in result.episodes if e.source_id == "chat-2210").mentions == 0
