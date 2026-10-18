"""The shipped examples stay valid: the schema loads, the episodes parse, and
selection runs on every text without a model."""

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
