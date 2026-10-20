"""A reply becomes a mention graph; graflo resolves it by keys."""

import uuid

import pytest
from conftest import DATA
from graflo.architecture.schema.identity_digest import compute_vertex_identity
from graflo.hq.mention_resolve import promote, resolve_mentions

from schemacast.episode import Episode
from schemacast.extract import WireExtraction
from schemacast.ground import groundings
from schemacast.schema_io import load_schema, schema_ref
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


def _check(plant, reply=REPLY):
    return validate(WireExtraction.model_validate(reply), plant, TEXT)


def _graph(plant, reply=REPLY, source_id="msg-1"):
    validation = _check(plant, reply)
    assert validation.removed == []
    return validation.graph(source_id, schema_ref(plant))


def _resolve(plant, reply=REPLY, source_id="msg-1"):
    return resolve_mentions(plant, [_graph(plant, reply, source_id)])


def test_a_reply_becomes_a_mention_graph(plant):
    graph = _graph(plant)
    assert [(m.ref, m.type, m.introduces) for m in graph.mentions] == [
        ("p1", "Person", True),
        ("m1", "Machine", True),
        ("s1", "Sensor", True),
        ("n1", "Note", True),
        ("w1", "WorkOrder", True),
        ("p2", "Person", False),
    ]
    assert graph.mentions[1].values() == {"serial": "S-7"}
    assert [(r.edge_id[2], r.source, r.target) for r in graph.relations] == [
        ("OPERATES", "p1", "m1"),
        ("MOUNTED_ON", "s1", "m1"),
        ("ABOUT", "n1", "m1"),
        ("CONCERNS", "w1", "m1"),
        ("OPERATES", "p2", "m1"),
    ]


def test_keys_identify_what_they_can_and_the_rest_is_staged(plant):
    resolution = _resolve(plant)
    sensor = plant.core_schema.vertex_config["Sensor"]
    identity = {r.ref: r.identity for r in resolution.resolved}
    assert identity["p1"] == {"email": "ann@x.com"}
    assert identity["s1"] == {"id": compute_vertex_identity({"tag": "T-1"}, sensor)}
    # Pinned: the key minted for a keyless entity never moves.
    assert identity["n1"] == {"id": "3587db98-ae28-5016-86fb-3d66a549861a"}
    uuid.UUID(identity["w1"]["id"])
    # Ben is named by phone only and nobody else states it.
    assert [(s.mention.ref, s.reason) for s in resolution.staged.mentions] == [
        ("p2", "unmatched")
    ]
    assert [h.waiting_on for h in resolution.staged.relations] == [["p2"]]
    container = promote(plant, resolution)
    assert container.edges[("Person", "Machine", "OPERATES")] == [
        ({"email": "ann@x.com"}, {"serial": "S-7"}, {})
    ]


def test_minted_keys_are_stable_and_scoped_to_the_episode(plant):
    first = _resolve(plant).identity_of("msg-1", "n1")
    assert _resolve(plant).identity_of("msg-1", "n1") == first
    assert _resolve(plant, source_id="msg-2").identity_of("msg-2", "n1") != first


def test_a_mention_with_a_key_keeps_what_the_text_states(plant):
    # Ann is referred to, not introduced, but the text states her key and name.
    reply = {
        "vertices": [
            _v(
                "p",
                "Person",
                "Ann (ann@x.com)",
                [("email", "ann@x.com", "ann@x.com"), ("name", "Ann", "Ann")],
                mention=True,
            )
        ],
        "edges": [],
    }
    resolution = _resolve(plant, reply)
    assert promote(plant, resolution).vertices["Person"] == [
        {"email": "ann@x.com", "name": "Ann"}
    ]


def test_an_entity_named_by_an_alternate_key_needs_no_repair(plant):
    reply = {
        "vertices": [
            _v("p", "Person", "Ben (+1 555)", [("phone", "+1 555", "+1 555")])
        ],
        "edges": [],
    }
    validation = _check(plant, reply)
    assert validation.findings == []
    resolution = resolve_mentions(plant, [validation.graph("msg-1", "plant@2.0.0")])
    assert [s.reason for s in resolution.staged.mentions] == ["unmatched"]


def test_an_entity_with_no_key_asks_one_repair_and_is_kept(plant):
    reply = {
        "vertices": [_v("p", "Person", "Ann", [("name", "Ann", "Ann")])],
        "edges": [],
    }
    validation = _check(plant, reply)
    (finding,) = validation.findings
    assert (finding.kind, finding.removes) == ("identity_insufficient", False)
    assert "email" in finding.message and "phone" in finding.message
    assert [m.ref for m in validation.mentions] == ["p"]
    resolution = resolve_mentions(plant, [validation.graph("msg-1", "plant@2.0.0")])
    assert [s.reason for s in resolution.staged.mentions] == ["no_key"]


def test_groundings_name_mention_refs(plant):
    episode = Episode(text=TEXT, source_id="msg-1")
    found = groundings(_graph(plant), episode, extractor="schemacast/test", model="m")
    kinds = [g.kind for g in found]
    assert kinds.count("vertex") == 6
    assert kinds.count("edge") == 5
    assert kinds.count("property") == 6
    for grounding in found:
        assert TEXT[grounding.char_start : grounding.char_end] == grounding.quote
        assert grounding.schema_ref == "plant@2.0.0"
    edge = next(g for g in found if g.edge and g.edge[0] == "Sensor")
    assert (edge.source_ref, edge.target_ref) == ("s1", "m1")
