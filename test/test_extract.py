"""One extraction call against a scripted endpoint."""

import jsonschema
import pytest
from conftest import DATA
from graflo.architecture.schema.context import instance_json_schema

from schemacast.config import CacheSettings
from schemacast.episode import Episode
from schemacast.extract import (
    INSTANCE_SCHEMA_NAME,
    EpisodeTooLongError,
    WireShapeError,
    extract,
)
from schemacast.prompt import build_messages
from schemacast.schema_io import load_schema

REPLY = {
    "vertices": [
        {
            "ref": "v1",
            "type": "Person",
            "quote": "Ann (ann@x.com)",
            "mention": False,
            "props": [{"name": "email", "value": "ann@x.com", "quote": "ann@x.com"}],
        },
        {
            "ref": "v2",
            "type": "Machine",
            "quote": "station S-7, rated 200 kW",
            "mention": False,
            "props": [
                {"name": "serial", "value": "S-7", "quote": "S-7"},
                {"name": "power", "value": "200 kW", "quote": "200 kW"},
            ],
        },
    ],
    "edges": [
        {
            "relation": "OPERATES",
            "from": "v1",
            "to": "v2",
            "quote": "Ann (ann@x.com) now operates station S-7",
            "props": [{"name": "since", "value": "2024-03", "quote": "2024-03"}],
        }
    ],
    "unmapped": [],
}


@pytest.fixture
def plant():
    return load_schema(DATA / "schema.yaml")


@pytest.fixture
def episode():
    return Episode(
        source_id="msg-1",
        text="Ann (ann@x.com) now operates station S-7, rated 200 kW, since 2024-03.",
    )


async def test_one_call_with_the_slice_prompt_and_shape(make_client, plant, episode):
    client, endpoint = make_client(REPLY)
    extraction = await extract(client, plant, episode)

    assert len(endpoint.requests) == 1
    request = endpoint.requests[0]
    assert request["messages"] == build_messages(plant, episode)
    assert request["response_format"]["json_schema"] == {
        "name": INSTANCE_SCHEMA_NAME,
        "schema": instance_json_schema(plant),
        "strict": True,
    }
    wire = extraction.wire
    assert extraction.source_id == "msg-1"
    assert [v.type for v in wire.vertices] == ["Person", "Machine"]
    assert wire.edges[0].from_ == "v1"
    assert wire.edges[0].props[0].value == "2024-03"
    assert not extraction.completion.cached


def test_reply_conforms_to_the_instance_schema(plant):
    jsonschema.validate(REPLY, instance_json_schema(plant))


async def test_replay_makes_no_request(make_client, plant, episode):
    client, _ = make_client(REPLY)
    first = await extract(client, plant, episode)
    replayer, endpoint = make_client(
        cache=CacheSettings(require_hit=True), api_key=None
    )
    second = await extract(replayer, plant, episode)
    assert endpoint.requests == []
    assert second.wire == first.wire
    assert second.completion.content == first.completion.content


async def test_missing_lists_read_as_empty_and_extra_keys_are_ignored(
    make_client, plant, episode
):
    client, _ = make_client({"vertices": [{"ref": "v1", "type": "Machine", "x": 1}]})
    extraction = await extract(client, plant, episode)
    assert extraction.wire.edges == []
    assert extraction.wire.unmapped == []
    assert extraction.wire.vertices[0].props == []


async def test_wrong_shape_is_a_typed_error(make_client, plant, episode):
    client, _ = make_client({"vertices": [{"type": "Machine"}]})
    with pytest.raises(WireShapeError) as info:
        await extract(client, plant, episode)
    assert info.value.source_id == "msg-1"
    assert '"type": "Machine"' in info.value.content


async def test_nested_values_are_refused(make_client, plant, episode):
    reply = {
        "vertices": [
            {
                "ref": "v1",
                "type": "Machine",
                "props": [{"name": "serial", "value": {"x": 1}, "quote": "x"}],
            }
        ]
    }
    client, _ = make_client(reply)
    with pytest.raises(WireShapeError):
        await extract(client, plant, episode)


async def test_over_ceiling_episode_is_refused_before_any_call(make_client, plant):
    client, endpoint = make_client(REPLY)
    long_episode = Episode(source_id="big", text="x" * 400)
    with pytest.raises(EpisodeTooLongError) as info:
        await extract(client, plant, long_episode, max_episode_tokens=50)
    assert info.value.estimated == 100
    assert endpoint.requests == []
