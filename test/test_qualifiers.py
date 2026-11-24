"""Qualifiers on edges and properties: when, modality and negated."""

import copy
import json
from datetime import UTC, datetime
from typing import Any

import jsonschema
import pytest
from conftest import DATA

from schemacast.cast import SchemaCaster
from schemacast.episode import Episode
from schemacast.extract import WireExtraction, episode_shape
from schemacast.ground import affirmed, groundings, modality_of, negated_of, when_of
from schemacast.prompt import system_message
from schemacast.repair import repair_prompt
from schemacast.schema_io import load_schema
from schemacast.validate import validate

TEXT = (
    "Ann (ann@x.com) operates press S-7 since this morning, not press S-9. "
    "Press S-7 will be rated 250 kW from 19 October 2026."
)
REFERENCE = datetime(2026, 10, 16, 9, 30, tzinfo=UTC)

REPLY: dict[str, Any] = {
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
                {
                    "name": "power",
                    "value": 250,
                    "quote": "250 kW",
                    "modality": "planned",
                    "when": {
                        "quote": "from 19 October 2026",
                        "start": "2026-10-19",
                        "end": None,
                        "granularity": "day",
                    },
                },
            ],
        },
        {
            "ref": "v3",
            "type": "Machine",
            "quote": "press S-9",
            "props": [{"name": "serial", "value": "S-9", "quote": "S-9"}],
        },
    ],
    "edges": [
        {
            "relation": "OPERATES",
            "from": "v1",
            "to": "v2",
            "quote": "operates press S-7",
            "when": {
                "quote": "since this morning",
                "start": "2026-10-16",
                "end": None,
                "granularity": "day",
            },
        },
        {
            "relation": "OPERATES",
            "from": "v1",
            "to": "v3",
            "quote": "not press S-9",
            "negated": True,
        },
    ],
}


@pytest.fixture
def plant():
    return load_schema(DATA / "plant.yaml")


def _check(plant, reply):
    return validate(WireExtraction.model_validate(reply), plant, TEXT)


def _with(path: list[Any], value: Any) -> dict[str, Any]:
    reply = copy.deepcopy(REPLY)
    target: Any = reply
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    return reply


def _episode() -> Episode:
    return Episode(source_id="m1", text=TEXT, reference_time=REFERENCE)


def test_a_reply_without_qualifiers_keeps_its_evidence_unchanged(plant):
    reply = copy.deepcopy(REPLY)
    del reply["edges"][1]
    edge = reply["edges"][0]
    del edge["when"]
    result = _check(plant, reply)
    assert set(result.relations[0].evidence or {}) == {"quote", "span"}
    assert modality_of(result.relations[0].evidence) == "actual"
    assert not negated_of(result.relations[0].evidence)


def test_null_qualifiers_read_as_the_defaults(plant):
    reply = copy.deepcopy(REPLY)
    del reply["edges"][1]
    reply["edges"][0].update(when=None, modality=None, negated=None)
    result = _check(plant, reply)
    assert result.findings == []
    assert set(result.relations[0].evidence or {}) == {"quote", "span"}


def test_qualifiers_are_checked_into_evidence(plant):
    result = _check(plant, REPLY)
    assert result.findings == []
    power = result.mentions[1].props["power"]
    assert modality_of(power.evidence) == "planned"
    when = when_of(power.evidence)
    assert when is not None
    assert (when.start, when.end, when.granularity) == ("2026-10-19", None, "day")
    assert TEXT[when.span.start : when.span.end] == "from 19 October 2026"
    runs, denied = result.relations
    assert when_of(runs.evidence) is not None
    assert negated_of(denied.evidence)


def test_a_denied_relation_is_a_statement_but_not_a_fact(plant):
    result = _check(plant, REPLY)
    statements = result.statements("m1", "plant@2.0.0")
    graph = result.graph("m1", "plant@2.0.0")
    assert len(statements.relations) == 2
    assert [r.target for r in graph.relations] == ["v2"]
    assert graph == affirmed(statements)


def test_a_denied_property_leaves_the_graph_with_its_value_grounded(plant):
    reply = _with(["vertices", 1, "props", 1, "negated"], True)
    result = _check(plant, reply)
    assert result.findings == []
    graph = result.graph("m1", "plant@2.0.0")
    assert "power" not in graph.records[1].props
    found = groundings(
        result.statements("m1", "plant@2.0.0"), _episode(), extractor="x"
    )
    power = next(g for g in found if g.property == "power")
    assert (power.value, power.negated, power.modality) == (250.0, True, "planned")


def test_groundings_carry_the_qualifiers(plant):
    result = _check(plant, REPLY)
    found = groundings(
        result.statements("m1", "plant@2.0.0", REFERENCE), _episode(), extractor="x"
    )
    edges = [g for g in found if g.kind == "edge"]
    assert [(g.target_ref, g.negated) for g in edges] == [("v2", False), ("v3", True)]
    assert edges[0].when is not None and edges[0].when.quote == "since this morning"
    vertex = next(g for g in found if g.kind == "vertex")
    assert (vertex.when, vertex.modality, vertex.negated) == (None, "actual", False)
    line = json.loads(edges[0].model_dump_json(exclude_none=True))
    assert line["when"]["start"] == "2026-10-16"


@pytest.mark.parametrize(
    ("path", "value", "detail"),
    [
        (["edges", 0, "modality"], "likely", "modality"),
        (["edges", 0, "when", "granularity"], "hour", "granularity"),
        (["edges", 0, "when", "granularity"], None, "granularity"),
        (["edges", 0, "when", "start"], "this morning", "not_iso"),
        (["edges", 0, "when", "start"], None, "not_iso"),
        (["edges", 0, "when", "end"], "16/10/2026", "not_iso"),
        (["edges", 0, "when", "end"], "2026-10-15", "start_after_end"),
        (["vertices", 1, "props", 0, "negated"], True, "negated_key"),
    ],
)
def test_a_malformed_qualifier_removes_its_item(plant, path, value, detail):
    result = _check(plant, _with(path, value))
    assert [(f.kind, f.detail) for f in result.removed] == [
        ("invalid_qualifier", detail)
    ]
    assert result.removed[0].path == ".".join(
        f"{k}[{i}]" for k, i in zip(path[:-1:2], path[1:-1:2], strict=False)
    )


def test_a_time_quote_must_be_in_the_text(plant):
    result = _check(plant, _with(["edges", 0, "when", "quote"], "since yesterday"))
    assert [(f.kind, f.detail, f.scope) for f in result.findings] == [
        ("quote_not_found", "when", "edge")
    ]
    assert len(result.relations) == 1


@pytest.mark.parametrize(
    ("start", "end"),
    [
        ("2026-10-16T18:00", "2026-10-16"),
        ("2026-10-16T18:00+02:00", "2026-10-16T19:00"),
        ("2026-10-16", None),
    ],
)
def test_dates_and_date_times_order_by_day_and_wall_clock(plant, start, end):
    reply = _with(["edges", 0, "when", "start"], start)
    reply["edges"][0]["when"]["end"] = end
    assert _check(plant, reply).findings == []


def test_iso_values_are_normalized(plant):
    reply = _with(["edges", 0, "when", "start"], "2026-10-16T18:00Z")
    when = when_of(_check(plant, reply).relations[0].evidence)
    assert when is not None and when.start == "2026-10-16T18:00:00+00:00"


def test_the_shape_admits_qualifiers_and_requires_every_key(plant):
    shape = episode_shape(plant)
    reply = copy.deepcopy(REPLY)
    for edge in reply["edges"]:
        edge.setdefault("when", None)
        edge.setdefault("modality", "actual")
        edge.setdefault("negated", False)
        edge.setdefault("props", [])
    for vertex in reply["vertices"]:
        vertex.setdefault("mention", False)
        for prop in vertex["props"]:
            prop.setdefault("when", None)
            prop.setdefault("modality", "actual")
            prop.setdefault("negated", False)
    reply["unmapped"] = []
    jsonschema.validate(reply, shape)
    del reply["edges"][0]["negated"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(reply, shape)


def test_the_prompt_explains_the_qualifiers(plant):
    rules = system_message(plant)["content"]
    for word in ("`when`", "`modality`", "`negated`", "reference time"):
        assert word in rules


def test_the_repair_prompt_explains_a_bad_qualifier(plant):
    result = _check(plant, _with(["edges", 0, "modality"], "likely"))
    prompt = repair_prompt(result.repairable)
    assert "## invalid_qualifier" in prompt
    assert "actual, planned or hedged" in prompt


async def test_a_cast_repairs_a_qualifier_and_writes_no_denial(make_client, plant):
    bad = _with(["edges", 0, "when", "end"], "2026-10-15")
    client, endpoint = make_client(bad, REPLY)
    result = await SchemaCaster(plant, client=client).cast([_episode()])

    episode = result.episodes[0]
    assert (episode.status, episode.repairs) == ("ok", 1)
    assert "## invalid_qualifier" in endpoint.requests[1]["messages"][-1]["content"]
    assert (
        "Reference time: 2026-10-16T09:30:00+00:00"
        in (endpoint.requests[0]["messages"][1]["content"])
    )
    edges = result.output.data.edges[("Person", "Machine", "OPERATES")]
    assert [target for _, target, _ in edges] == [{"serial": "S-7"}]
    assert [g.negated for g in result.groundings if g.kind == "edge"] == [False, True]
