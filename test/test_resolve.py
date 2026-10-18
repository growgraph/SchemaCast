"""Identities before edges, mentions never minted, groundings per assertion."""

import uuid

import pytest
from conftest import DATA
from graflo.architecture.schema.identity_digest import compute_vertex_identity

from schemacast.episode import Episode
from schemacast.extract import WireExtraction
from schemacast.resolve import merge_containers, mint_id, resolve
from schemacast.schema_io import load_schema
from schemacast.validate import validate

TEXT = (
    "Ann (ann@x.com) operates press S-7. Sensor T-1 is on S-7. "
    "Note: S-7 leaks. A work order: replace seal. Ben (+1 555) runs S-7."
)


@pytest.fixture
def plant():
    return load_schema(DATA / "plant.yaml")


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


REPLY = {
    "vertices": [
        _v("p1", "Person", "Ann (ann@x.com)", [("email", "ann@x.com", "ann@x.com")]),
        _v("m1", "Machine", "press S-7", [("serial", "S-7", "S-7")]),
        _v("s1", "Sensor", "Sensor T-1", [("tag", "T-1", "T-1")]),
        _v("n1", "Note", "S-7 leaks", [("body", "S-7 leaks", "S-7 leaks")]),
        _v(
            "w1",
            "WorkOrder",
            "A work order: replace seal",
            [("summary", "replace seal", "replace seal")],
        ),
        _v(
            "p2",
            "Person",
            "Ben (+1 555)",
            [("phone", "+1 555", "+1 555")],
            mention=True,
        ),
    ],
    "edges": [
        _e("OPERATES", "p1", "m1", "Ann (ann@x.com) operates press S-7"),
        _e("MOUNTED_ON", "s1", "m1", "Sensor T-1 is on S-7"),
        _e("ABOUT", "n1", "m1", "S-7 leaks"),
        _e("CONCERNS", "w1", "m1", "work order"),
        _e("OPERATES", "p2", "m1", "Ben (+1 555) runs S-7"),
    ],
}


def _resolve(plant, reply=REPLY, source_id="msg-1"):
    episode = Episode(text=TEXT, source_id=source_id)
    validation = validate(WireExtraction.model_validate(reply), plant, TEXT)
    assert validation.findings == []
    return resolve(validation, plant, episode, extractor="schemacast/test", model="m")


def test_vertex_documents_carry_their_keys(plant):
    container = _resolve(plant).container
    sensor = plant.core_schema.vertex_config["Sensor"]
    assert container.vertices["Person"] == [{"email": "ann@x.com"}]
    assert container.vertices["Sensor"] == [
        {"tag": "T-1", "id": compute_vertex_identity({"tag": "T-1"}, sensor)}
    ]
    assert container.vertices["Note"][0]["id"] == mint_id(plant, "msg-1", "Note", "n1")
    uuid.UUID(container.vertices["WorkOrder"][0]["id"])


def test_minted_keys_are_stable_and_scoped_to_the_episode(plant):
    first = _resolve(plant).container.vertices["Note"][0]["id"]
    assert _resolve(plant).container.vertices["Note"][0]["id"] == first
    assert (
        _resolve(plant, source_id="msg-2").container.vertices["Note"][0]["id"] != first
    )


def test_edges_use_identity_documents(plant):
    edges = _resolve(plant).container.edges
    sensor_id = compute_vertex_identity(
        {"tag": "T-1"}, plant.core_schema.vertex_config["Sensor"]
    )
    assert edges[("Person", "Machine", "OPERATES")] == [
        ({"email": "ann@x.com"}, {"serial": "S-7"}, {})
    ]
    assert edges[("Sensor", "Machine", "MOUNTED_ON")] == [
        ({"id": sensor_id}, {"serial": "S-7"}, {})
    ]


def test_a_mention_is_never_written_as_a_vertex(plant):
    reply = {
        "vertices": [
            _v("m1", "Machine", "press S-7", [("serial", "S-7", "S-7")], mention=True),
            _v("n1", "Note", "S-7 leaks", [("body", "S-7 leaks", "S-7 leaks")]),
        ],
        "edges": [_e("ABOUT", "n1", "m1", "S-7 leaks")],
    }
    resolution = _resolve(plant, reply)
    assert "Machine" not in resolution.container.vertices
    assert resolution.container.edges[("Note", "Machine", "ABOUT")][0][1] == {
        "serial": "S-7"
    }


def test_a_secondary_identity_mention_makes_the_edge_pending(plant):
    resolution = _resolve(plant)
    assert len(resolution.pending) == 1
    pending = resolution.pending[0]
    assert pending.edge_id == ("Person", "Machine", "OPERATES")
    assert pending.source.match == {"phone": "+1 555"}
    assert pending.source.identity is None
    assert pending.target.identity == {"serial": "S-7"}
    assert len(resolution.container.edges[("Person", "Machine", "OPERATES")]) == 1


def test_every_assertion_is_grounded_at_its_quote(plant):
    groundings = _resolve(plant).groundings
    kinds = [g.kind for g in groundings]
    assert kinds.count("vertex") == 6
    assert kinds.count("edge") == 5
    assert kinds.count("property") == 6
    for grounding in groundings:
        assert TEXT[grounding.char_start : grounding.char_end] == grounding.quote
        assert grounding.schema_ref == "plant@2.0.0"
        assert grounding.extractor == "schemacast/test"
    edge = next(g for g in groundings if g.kind == "edge" and g.edge[0] == "Sensor")
    assert edge.target_identity == {"serial": "S-7"}


def test_merge_keeps_order(plant):
    a = _resolve(plant).container
    b = _resolve(plant, source_id="msg-2").container
    merged = merge_containers([a, b])
    assert len(merged.vertices["Note"]) == 2
    assert merged.vertices["Note"][0] == a.vertices["Note"][0]
