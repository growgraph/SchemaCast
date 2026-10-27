"""Stage A selection: lexical lanes, fusion, the gate, and closure."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import DATA
from graflo.architecture.schema.core import CoreSchema
from graflo.architecture.schema.document import Schema
from graflo.architecture.schema.edge import Edge, EdgeConfig
from graflo.architecture.schema.metadata import GraphMetadata
from graflo.architecture.schema.semantics import FieldSemantics, Semantics
from graflo.architecture.schema.vertex import Field, Vertex, VertexConfig

from schemacast.schema_io import load_schema
from schemacast.select import (
    SelectionConfig,
    SelectionError,
    number_adjacent_tokens,
    retrieve,
    select,
    words,
)


@pytest.fixture
def plant():
    return load_schema(DATA / "schema.yaml")


@pytest.fixture
def large():
    """A plant schema among many distractor types, too large to pass whole."""
    vertices = [
        Vertex(
            name="Machine",
            semantics=Semantics(synonyms=["station"]),
            properties=[
                Field(name="serial", type="string"),
                Field(name="power", type="float", semantics=FieldSemantics(unit="kW")),
                Field(
                    name="service_interval",
                    type="int",
                    semantics=FieldSemantics(unit="day"),
                ),
            ],
            identity=["serial"],
        ),
        Vertex(
            name="Person",
            properties=[Field(name="email", type="string")],
            identity=["email"],
        ),
        Vertex(
            name="PLC",
            description="Programmable logic controller of a machine.",
            properties=[Field(name="firmware", type="string")],
            identity=["firmware"],
        ),
    ]
    vertices += [
        Vertex(
            name=f"Ledger{i:02d}",
            description=f"Accounting ledger number {i}.",
            semantics=Semantics(synonyms=["ledger"]),
            properties=[Field(name=f"code{j}", type="string") for j in range(6)],
            identity=["code0"],
        )
        for i in range(40)
    ]
    edges = [
        Edge(source="Person", target="Machine", relation="OPERATES"),
        Edge(source="PLC", target="Machine", relation="CONTROLS"),
    ]
    edges += [
        Edge(
            source=f"Ledger{i:02d}", target=f"Ledger{(i + 1) % 40:02d}", relation="NEXT"
        )
        for i in range(40)
    ]
    return Schema(
        metadata=GraphMetadata(name="large", version="1.0.0"),
        core_schema=CoreSchema(
            vertex_config=VertexConfig(vertices=vertices),
            edge_config=EdgeConfig(edges=edges),
        ),
    )


def _seeds(schema, text, **config):
    hits, metrics = retrieve(schema, text, SelectionConfig(**config))
    return [hit.seed for hit in hits], metrics


def test_words_split_case_and_plurals():
    assert words("WorkOrders for tax_id HTTPServer") == [
        "work",
        "order",
        "for",
        "tax",
        "id",
        "http",
        "server",
    ]


def test_number_adjacent_tokens():
    assert number_adjacent_tokens("rated 200 kW, 4-15 days, 0.5 % and 3 of them") == {
        "kW",
        "days",
        "%",
    }


def test_synonym_and_unit_lanes_agree(plant):
    hits, metrics = retrieve(plant, "Station S-7 is rated 200 kW.")
    assert hits[0].seed == "Machine"
    assert hits[0].ranks == {"lexical": 1, "unit": 1}
    assert metrics.lane_hits["unit"] == 1


def test_unit_lane_is_case_insensitive_and_plural_tolerant(large):
    assert _seeds(large, "draws 12 KW")[0] == ["Machine"]
    assert _seeds(large, "serviced every 30 days")[0] == ["Machine"]


def test_short_unit_tokens_are_not_plural_stripped():
    assert number_adjacent_tokens("5 ms") == {"ms"}


def test_camel_case_name_matches_as_a_phrase(plant):
    seeds, _ = _seeds(plant, "Opened a work order today")
    assert seeds == ["WorkOrder"]


def test_code_like_name_matches_exactly(large):
    hits, _ = retrieve(large, "The PLC rebooted.")
    assert hits[0].seed == "PLC"
    assert "exact" in hits[0].ranks


def test_edge_relation_seeds_the_edge(plant):
    seeds, _ = _seeds(plant, "who operates it?")
    assert ("Person", "Machine", "OPERATES") in seeds


def test_rare_phrases_outrank_shared_ones(large):
    seeds, _ = _seeds(large, "the ledger and the station")
    assert seeds[0] == "Machine"


def test_gate_is_relative(large):
    _, metrics = _seeds(large, "the ledger and the station", min_relative_score=1.0)
    assert metrics.seeds == 1
    assert metrics.gated_out > 0


def test_cap_counts_what_it_drops(large):
    _, metrics = _seeds(large, "the ledger and the station", max_seeds=2)
    assert metrics.seeds == 2
    assert metrics.capped > 0


def test_retrieval_is_deterministic(large):
    text = "the ledger, the PLC and station S-7 at 12 kW"
    first = retrieve(large, text)
    for _ in range(5):
        assert retrieve(large, text) == first


def test_pinned_types_seed_in_name_order(large):
    text = "Ann operates station S-7."
    config = SelectionConfig(max_elements=3)
    first = select(large, text, config, pinned=["PLC", "Person", "PLC"])
    second = select(large, text, config, pinned=["Person", "PLC"])
    assert first.pinned == second.pinned == ["PLC", "Person"]
    assert first.slice.to_minimal_canonical_dict() == (
        second.slice.to_minimal_canonical_dict()
    )


def test_lexical_scores_do_not_depend_on_the_hash_seed():
    script = (
        "from conftest import DATA\n"
        "from schemacast.schema_io import load_schema\n"
        "from schemacast.select import retrieve\n"
        "hits, _ = retrieve(load_schema(DATA / 'plant.yaml'), "
        "'The press station operates; a work order for the sensor tag.')\n"
        "print([(str(h.seed), h.score.hex()) for h in hits])\n"
    )
    runs = {
        subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            check=True,
            cwd=Path(__file__).parent,
            env={**os.environ, "PYTHONHASHSEED": seed},
        ).stdout
        for seed in ("1", "2", "3")
    }
    assert len(runs) == 1


def test_small_schema_passes_whole(plant):
    selection = select(plant, "nothing relevant here")
    assert selection.mode == "whole"
    assert selection.slice is plant
    assert selection.seeds == []


def test_large_schema_is_sliced_around_the_seeds(large):
    selection = select(
        large, "Ann operates station S-7, rated 12 kW.", SelectionConfig(max_tokens=600)
    )
    names = {v.name for v in selection.slice.core_schema.vertex_config.vertices}
    assert selection.mode == "slice"
    assert {"Machine", "Person"} <= names
    assert not any(name.startswith("Ledger") for name in names)
    assert selection.elision is not None
    assert ("Person", "Machine", "OPERATES") in selection.slice.core_schema.edge_config


def test_no_match_on_a_large_schema_is_a_reported_failure(large):
    with pytest.raises(SelectionError) as info:
        select(large, "completely unrelated words", SelectionConfig(max_tokens=600))
    assert info.value.reason == "no_match"
    assert info.value.metrics is not None
    assert info.value.metrics.fused == 0


def test_empty_text_is_refused(plant):
    with pytest.raises(SelectionError) as info:
        select(plant, "   ")
    assert info.value.reason == "empty_text"


@pytest.mark.parametrize(
    ("forms", "stem"),
    [
        (("route", "routes", "routed", "routing"), "rout"),
        (("battery", "batteries"), "battery"),
        (("box", "boxes"), "box"),
        (("ship", "shipped", "shipping"), "ship"),
        (("install", "installing", "installed"), "install"),
        (("address", "addresses"), "address"),
    ],
)
def test_suffixes_meet_at_one_stem(forms, stem):
    assert {words(form)[0] for form in forms} == {stem}


def test_short_words_are_not_stemmed():
    assert words("bus use ms") == ["bus", "use", "ms"]
