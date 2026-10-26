"""Validation: every defect is a finding, deterministic fixes are recorded."""

import copy
from typing import Any

import pytest
from conftest import DATA

from schemacast.extract import WireExtraction
from schemacast.ground import span_of
from schemacast.schema_io import load_schema
from schemacast.validate import validate

TEXT = "Ann (ann@x.com) operates press S-7, rated 200 kW, since 2024-03. Call +1 555."

GOOD: dict[str, Any] = {
    "vertices": [
        {
            "ref": "v1",
            "type": "Person",
            "quote": "Ann (ann@x.com)",
            "props": [{"name": "email", "value": "ann@x.com", "quote": "ann@x.com"}],
        },
        {
            "ref": "v2",
            "type": "Machine",
            "quote": "press S-7",
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
            "quote": "operates press S-7",
            "props": [{"name": "since", "value": "2024-03", "quote": "2024-03"}],
        }
    ],
    "unmapped": [{"quote": "Call +1 555", "note": "no call type"}],
}


@pytest.fixture
def plant():
    return load_schema(DATA / "plant.yaml")


def _check(plant, reply, text=TEXT):
    return validate(WireExtraction.model_validate(reply), plant, text)


def _mutate(path, value):
    reply = copy.deepcopy(GOOD)
    target = reply
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    return reply


def _kinds(result):
    return [(f.kind, f.scope, f.path) for f in result.findings]


def test_a_good_reply_passes_with_coercion_recorded(plant):
    result = _check(plant, GOOD)
    assert result.findings == []
    assert [v.values() for v in result.mentions] == [
        {"email": "ann@x.com"},
        {"serial": "S-7", "power": 200.0},
    ]
    assert result.relations[0].edge_id == ("Person", "Machine", "OPERATES")
    assert result.relations[0].values() == {"since": "2024-03"}
    assert [(f.kind, f.path) for f in result.fixes] == [
        ("coerced", "vertices[1].props[1]")
    ]
    assert result.unmapped[0].span.start == TEXT.index("Call +1 555")


def test_an_unmapped_quote_outside_the_text_is_dropped(plant):
    reply = copy.deepcopy(GOOD)
    reply["unmapped"].append(
        {"quote": "Written at: 2024-03-04T09:15:00+00:00", "note": "a timestamp"}
    )
    result = _check(plant, reply)
    assert [u.quote for u in result.unmapped] == ["Call +1 555"]


def test_spans_point_at_the_quotes(plant):
    result = _check(plant, GOOD)
    machine = result.mentions[1]
    span = span_of(machine.evidence)
    assert span is not None and TEXT[span.start : span.end] == "press S-7"
    power = span_of(machine.props["power"].evidence)
    assert power is not None
    assert TEXT[power.start : power.end] == "200 kW"


def test_unknown_type_drops_the_vertex_and_its_edges(plant):
    result = _check(plant, _mutate(("vertices", 0, "type"), "Robot"))
    assert _kinds(result) == [
        ("unknown_type", "vertex", "vertices[0]"),
        ("endpoint_invalid", "edge", "edges[0]"),
    ]
    assert [f.kind for f in result.repairable] == ["unknown_type"]
    assert result.relations == []


def test_duplicate_ref(plant):
    reply = copy.deepcopy(GOOD)
    reply["vertices"][1]["ref"] = "v1"
    assert ("duplicate_ref", "vertex", "vertices[1]") in _kinds(_check(plant, reply))


def test_property_of_another_type_is_undeclared(plant):
    reply = _mutate(
        ("vertices", 1, "props", 1), {"name": "email", "value": "x", "quote": "S-7"}
    )
    result = _check(plant, reply)
    assert _kinds(result) == [
        ("undeclared_property", "property", "vertices[1].props[1]")
    ]
    assert result.mentions[1].values() == {"serial": "S-7"}


def test_bad_value_is_a_coercion_finding_with_its_kind(plant):
    reply = _mutate(
        ("vertices", 1, "props", 1),
        {"name": "power", "value": "0.2 MW", "quote": "200 kW"},
    )
    finding = _check(plant, reply).findings[0]
    assert (finding.kind, finding.detail) == ("coercion", "unit_mismatch")


def test_duplicate_property(plant):
    reply = copy.deepcopy(GOOD)
    reply["vertices"][1]["props"].append(
        {"name": "serial", "value": "S-8", "quote": "S-7"}
    )
    assert _kinds(_check(plant, reply)) == [
        ("duplicate_property", "property", "vertices[1].props[2]")
    ]


def test_fabricated_quotes_are_findings(plant):
    result = _check(plant, _mutate(("vertices", 1, "quote"), "the blue press"))
    assert ("quote_not_found", "vertex", "vertices[1]") in _kinds(result)

    reply = _mutate(("edges", 0, "props", 0, "quote"), "March 2024")
    assert _kinds(_check(plant, reply)) == [
        ("quote_not_found", "property", "edges[0].props[0]")
    ]


def test_normalised_quote_match_is_a_fix(plant):
    result = _check(plant, _mutate(("vertices", 1, "quote"), "PRESS  s-7"))
    assert result.findings == []
    assert ("quote_normalized", "vertices[1]") in [
        (f.kind, f.path) for f in result.fixes
    ]


def test_natural_identity_needs_a_key(plant):
    reply = _mutate(("vertices", 0, "props"), [])
    result = _check(plant, reply)
    assert _kinds(result)[0] == ("identity_insufficient", "vertex", "vertices[0]")


def test_dropping_an_identity_value_drops_the_vertex(plant):
    reply = _mutate(
        ("vertices", 0, "props"), [{"name": "email", "value": "x", "quote": "not here"}]
    )
    kinds = {k for k, _, _ in _kinds(_check(plant, reply))}
    assert {"quote_not_found", "identity_insufficient"} <= kinds


def test_hash_identity_needs_its_source_fields(plant):
    reply = {"vertices": [{"ref": "s1", "type": "Sensor", "quote": "S-7", "props": []}]}
    assert _kinds(_check(plant, reply)) == [
        ("identity_insufficient", "vertex", "vertices[0]")
    ]


def test_assigned_and_blank_types_need_no_key(plant):
    reply = {
        "vertices": [
            {"ref": "w1", "type": "WorkOrder", "quote": "S-7", "props": []},
            {"ref": "n1", "type": "Note", "quote": "S-7", "props": []},
        ]
    }
    assert _check(plant, reply).findings == []


def test_a_reference_without_a_key_is_kept_for_resolution(plant):
    # A referred-to entity is never a finding, whatever it states: graflo
    # resolves or stages it.
    by_name = {
        "ref": "v1",
        "type": "Person",
        "quote": "Ann",
        "mention": True,
        "props": [{"name": "name", "value": "Ann", "quote": "Ann"}],
    }
    work_order = {"ref": "w", "type": "WorkOrder", "quote": "S-7", "mention": True}
    result = _check(plant, {"vertices": [by_name, work_order]})
    assert result.findings == []
    assert [(m.ref, m.may_create) for m in result.mentions] == [
        ("v1", False),
        ("w", False),
    ]


def test_reversed_edge_is_flipped_and_recorded(plant):
    reply = copy.deepcopy(GOOD)
    reply["edges"][0].update({"from": "v2", "to": "v1"})
    result = _check(plant, reply)
    assert result.findings == []
    edge = result.relations[0]
    assert (edge.source, edge.target) == ("v1", "v2")
    assert [f.kind for f in result.fixes if f.path == "edges[0]"] == ["edge_reversed"]


def test_undeclared_relation(plant):
    result = _check(plant, _mutate(("edges", 0, "relation"), "OWNS"))
    assert _kinds(result) == [("undeclared_edge", "edge", "edges[0]")]


def test_dangling_ref(plant):
    result = _check(plant, _mutate(("edges", 0, "to"), "v9"))
    assert _kinds(result) == [("dangling_ref", "edge", "edges[0]")]


def test_findings_are_ordered_by_kind_and_carry_fragments(plant):
    reply = copy.deepcopy(GOOD)
    reply["vertices"][1]["quote"] = "nowhere"
    reply["vertices"][0]["type"] = "Robot"
    result = _check(plant, reply)
    assert [f.kind for f in result.findings][:2] == ["unknown_type", "quote_not_found"]
    assert result.findings[0].fragment["type"] == "Robot"


def test_a_value_its_quote_does_not_state_is_a_finding(plant):
    text = "Ann (ann@x.com) operates press S-7, rated 3.75 kW, since 2024-03."
    reply = _mutate(
        ["vertices", 1, "props", 1],
        {"name": "power", "value": 37.5, "quote": "3.75 kW"},
    )
    result = _check(plant, reply, text)
    [finding] = result.findings
    assert (finding.kind, finding.path) == (
        "value_not_in_quote",
        "vertices[1].props[1]",
    )
    machine = next(v for v in result.mentions if v.type == "Machine")
    assert "power" not in machine.props


def test_a_date_part_its_quote_does_not_state_is_a_finding(plant):
    reply = _mutate(
        ["edges", 0, "props", 0],
        {"name": "since", "value": "2024-03-01", "quote": "2024-03"},
    )
    [finding] = _check(plant, reply).findings
    assert finding.kind == "value_not_in_quote"


def test_an_identity_value_grounds_in_context(plant):
    from schemacast.episode import ContextItem

    text = "The press is leaking again."
    context = [ContextItem(source_id="m0", text="Press S-7 was serviced on Monday.")]
    reply = {
        "vertices": [
            {
                "ref": "v1",
                "type": "Machine",
                "quote": "The press",
                "mention": True,
                "props": [{"name": "serial", "value": "S-7", "quote": "S-7"}],
            }
        ]
    }
    result = validate(WireExtraction.model_validate(reply), plant, text, context)
    assert result.findings == []
    span = span_of(result.mentions[0].props["serial"].evidence)
    assert span is not None and span.context == "m0"
    assert context[0].text[span.start : span.end] == "S-7"
    assert [f.kind for f in result.fixes] == ["grounded_in_context"]


def test_a_non_identity_value_does_not_ground_in_context(plant):
    from schemacast.episode import ContextItem

    text = "Press S-7 is leaking again."
    context = [ContextItem(source_id="m0", text="It is rated 200 kW.")]
    reply = {
        "vertices": [
            {
                "ref": "v1",
                "type": "Machine",
                "quote": "Press S-7",
                "props": [
                    {"name": "serial", "value": "S-7", "quote": "S-7"},
                    {"name": "power", "value": 200, "quote": "200 kW"},
                ],
            }
        ]
    }
    result = validate(WireExtraction.model_validate(reply), plant, text, context)
    assert [f.kind for f in result.findings] == ["quote_not_found"]


def _person(quote, *props):
    return {
        "vertices": [
            {
                "ref": "v1",
                "type": "Person",
                "quote": quote,
                "props": [
                    {"name": name, "value": value, "quote": value}
                    for name, value in props
                ],
            }
        ]
    }


def test_a_second_key_a_sentence_apart_is_removed(plant):
    reply = _person("Ann (ann@x.com)", ("email", "ann@x.com"), ("phone", "+1 555"))

    result = _check(plant, reply)

    (finding,) = result.findings
    assert (finding.kind, finding.scope, finding.path) == (
        "keys_apart",
        "property",
        "vertices[0].props[1]",
    )
    assert [m.values() for m in result.mentions] == [{"email": "ann@x.com"}]


def test_two_keys_in_one_sentence_are_kept(plant):
    text = "Reach Ann at ann@x.com or +1 555 today."
    reply = _person(
        "Ann at ann@x.com or +1 555", ("email", "ann@x.com"), ("phone", "+1 555")
    )

    result = _check(plant, reply, text)

    assert result.findings == []
    assert result.mentions[0].values() == {"email": "ann@x.com", "phone": "+1 555"}


def test_one_key_beside_its_mention_is_kept(plant):
    result = _check(plant, _person("Ann", ("email", "ann@x.com")))

    assert result.findings == []
    assert result.mentions[0].values() == {"email": "ann@x.com"}


def test_a_wider_quote_does_not_join_keys_a_sentence_apart(plant):
    quote = (
        "Ann (ann@x.com) operates press S-7, rated 200 kW, since 2024-03. Call +1 555"
    )
    reply = _person(quote, ("email", "ann@x.com"), ("phone", "+1 555"))

    result = _check(plant, reply)

    assert [f.kind for f in result.findings] == ["keys_apart"]
    assert result.mentions[0].values() == {"email": "ann@x.com"}
