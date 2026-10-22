"""The benchmark: an exact corpus, a scorer that needs no judge, and its command."""

import asyncio
import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from graflo.architecture.schema.key_contract import key_contract, key_status

from schemacast.bench.cli import main as bench
from schemacast.bench.corpus import (
    CORPUS_DIGEST,
    CORPUS_VERSION,
    BenchCase,
    CaseSettings,
    corpus_digest,
    load_corpus,
)
from schemacast.bench.oracle import oracle_reply
from schemacast.bench.report import case_score, rates
from schemacast.bench.run import Row, run_row
from schemacast.bench.score import gold_vertices, score_episode, score_recast
from schemacast.cast import SchemaCaster
from schemacast.config import CacheSettings, SchemaCastSettings
from schemacast.ground import locate
from schemacast.prompt import known_refs

CORPUS = load_corpus()
BY_NAME = {case.name: case for case in CORPUS}


def test_the_corpus_digest_is_pinned_to_its_version():
    assert corpus_digest() == CORPUS_DIGEST, (
        f"cases or schemas changed: bump CORPUS_VERSION ({CORPUS_VERSION}) and "
        f"set CORPUS_DIGEST to {corpus_digest()}"
    )


def test_every_family_has_two_dev_and_two_test_instances():
    families = Counter((case.family, case.split) for case in CORPUS)
    names = {case.family for case in CORPUS}
    assert len(names) == 10
    for family in names:
        per_half = 1 if family == "known" else 2
        assert families[(family, "dev")] == per_half
        assert families[(family, "test")] == per_half


@pytest.mark.parametrize("case", CORPUS, ids=lambda case: case.name)
def test_the_expected_graph_is_in_the_text(case: BenchCase):
    config = case.schema_.core_schema.vertex_config
    edge_ids = {edge.edge_id for edge in case.schema_.core_schema.edge_config.edges}
    for episode in case.episodes:
        expected = case.expected[episode.source_id]
        text = episode.text
        context = {item.source_id: item.text for item in episode.context}
        for graph in (expected, expected.forbidden, expected.allowed):
            gold_vertices(graph, case.schema_, text, context)  # values
            for handle, vertex in graph.vertices.items():
                assert locate(vertex.quote, text), (episode.source_id, handle)
                identity = key_contract(config[vertex.type]).key_fields
                for name, prop in vertex.props.items():
                    source = text
                    if prop.context is not None:
                        assert name in identity, (episode.source_id, handle, name)
                        source = context[prop.context]
                    assert locate(prop.evidence, source), (
                        episode.source_id,
                        handle,
                        name,
                    )
            kinds = {
                ref: entity.vertex_type
                for ref, entity in known_refs(episode.known, case.schema_).items()
            }
            kinds.update(
                {h: v.type for h, v in {**expected.vertices, **graph.vertices}.items()}
            )
            for edge in graph.edges:
                assert locate(edge.quote, text), (episode.source_id, edge.relation)
                edge_id = (kinds[edge.from_], kinds[edge.to], edge.relation)
                assert edge_id in edge_ids, (episode.source_id, edge_id)
                for name, prop in edge.props.items():
                    assert locate(prop.evidence, text), (episode.source_id, name)
        for handle, vertex in expected.vertices.items():
            gold = gold_vertices(expected, case.schema_, text, context)[handle]
            if vertex.mention:
                status = key_status(key_contract(config[vertex.type]), gold.props)
                assert status.state in ("primary", "alternate"), handle
            elif config[vertex.type].identity_mode == "natural":
                assert gold.keys and gold.keys[0][0] == "id", handle


def _run(cases: list[BenchCase], settings: SchemaCastSettings, **kwargs: Any) -> Row:
    return asyncio.run(run_row(cases, settings, **kwargs))


def test_the_oracle_scores_perfectly_on_the_whole_schema():
    unbudgeted = [
        case.model_copy(update={"settings": CaseSettings()}) for case in CORPUS
    ]
    settings = SchemaCastSettings(slice_max_elements=None, slice_max_tokens=None)
    row = _run(unbudgeted, settings, oracle=True)
    for case in row.cases:
        counts = case.counts
        assert case_score(counts) == 1.0, case.name
        assert counts.failed == 0 and counts.repairs == 0, case.name
        assert counts.forbidden_written == 0, case.name
        assert counts.spans_hit == counts.spans_checked, case.name
        assert counts.mention_agree == counts.entities_matched, case.name
        assert counts.edges_reversed == 0 and counts.nonconforming_first == 0, case.name
        assert counts.recast_added == 0 and counts.recast_duplicated == 0, case.name
        assert counts.picks_correct == counts.picks_expected, case.name
        assert counts.picks_offered == counts.picks_expected, case.name
        assert counts.picks_wrong == 0 and counts.picks_rejected == 0, case.name
    recast = sum(case.counts.recast_vertices for case in row.cases)
    assert recast > 0
    assert row.counts.forbidden_expected > 0
    assert row.counts.picks_expected == 4
    assert row.counts.picks_linked == 2


def test_the_oracle_row_reads_selection_under_the_case_budget():
    row = _run([BY_NAME["distractors/plant"]], SchemaCastSettings(), oracle=True)
    counts = row.counts
    assert counts.sliced == 2
    assert counts.gold_types == 4
    assert counts.gold_types_in_slice <= counts.gold_types


def _expected_reply(case: BenchCase, source_id: str) -> dict[str, Any]:
    return oracle_reply(case.expected[source_id])


async def _cast_one(case: BenchCase, source_id: str, client: Any):
    caster = SchemaCaster(case.schema_, settings=SchemaCastSettings(), client=client)
    episode = next(e for e in case.episodes if e.source_id == source_id)
    return await caster.cast_episode(episode), episode


async def test_an_edge_the_wrong_way_round_counts_as_reversed(make_client):
    case = BY_NAME["direction/plant"]
    reply = _expected_reply(case, "direction-plant-1")
    edge = reply["edges"][0]
    edge["from"], edge["to"] = edge["to"], edge["from"]
    client, _ = make_client(reply)
    outcome, episode = await _cast_one(case, "direction-plant-1", client)
    counts = score_episode(
        outcome, case.expected[episode.source_id], case.schema_, episode.text
    )
    assert counts.edges_reversed == 1
    assert counts.edges_matched == 0
    assert rates(counts)["direction"] == 0.0
    assert counts.entities_matched == 2


async def test_an_obeyed_injection_is_written_and_counted(make_client):
    case = BY_NAME["injection/plant"]
    source_id = "injection-plant-1"
    reply = _expected_reply(case, source_id)
    forbidden = case.expected[source_id].forbidden
    reply["vertices"].append(
        {
            "ref": "m",
            "type": "Person",
            "quote": "mallory@evil.example",
            "mention": False,
            "props": [
                {
                    "name": "email",
                    "value": "mallory@evil.example",
                    "quote": "mallory@evil.example",
                }
            ],
        }
    )
    reply["edges"].append(
        {
            "relation": "OPERATES",
            "from": "m",
            "to": "s",
            "quote": forbidden.edges[0].quote,
            "props": [],
        }
    )
    client, _ = make_client(reply)
    outcome, episode = await _cast_one(case, source_id, client)
    counts = score_episode(
        outcome, case.expected[source_id], case.schema_, episode.text
    )
    assert counts.forbidden_expected == 2
    assert counts.forbidden_written == 2
    assert counts.entities_matched == 1
    assert counts.entities_produced == 2


async def test_a_hallucinated_type_is_counted_on_the_first_reply(make_client):
    case = BY_NAME["units/plant"]
    source_id = "units-plant-1"
    good = _expected_reply(case, source_id)
    bad = json.loads(json.dumps(good))
    bad["vertices"].append(
        {
            "ref": "x",
            "type": "Robot",
            "quote": "Press S-12",
            "mention": False,
            "props": [],
        }
    )
    client, endpoint = make_client(bad, good)
    outcome, episode = await _cast_one(case, source_id, client)
    counts = score_episode(
        outcome, case.expected[source_id], case.schema_, episode.text
    )
    assert counts.unknown_type_first == 1
    assert counts.vertices_first == 2
    assert counts.repairs == 1
    assert counts.nonconforming_final == 0
    assert case_score(counts) == 1.0
    assert len(endpoint.requests) == 2


async def test_a_value_the_field_refuses_loses_the_property(make_client):
    case = BY_NAME["units/plant"]
    source_id = "units-plant-1"
    reply = _expected_reply(case, source_id)
    for prop in reply["vertices"][0]["props"]:
        if prop["name"] == "commissioned":
            prop["value"] = "4 March 2024"
    client, endpoint = make_client(reply, reply, reply)
    outcome, episode = await _cast_one(case, source_id, client)
    counts = score_episode(
        outcome, case.expected[source_id], case.schema_, episode.text
    )
    assert counts.props_expected == 3
    assert counts.props_matched == 2
    # The repair returned the same findings, so no second repair is asked for.
    assert counts.repairs == 1
    assert len(endpoint.requests) == 2


async def test_a_re_cast_that_renames_a_ref_duplicates_an_assigned_vertex(make_client):
    case = BY_NAME["recast/plant"]
    source_id = "recast-plant-1"
    first = _expected_reply(case, source_id)
    second = json.loads(json.dumps(first).replace('"wo"', '"order"'))
    client_a, _ = make_client(first)
    # Its own cache, as the runner gives the second cast: the request is the same.
    client_b, _ = make_client(second, cache=CacheSettings(enabled=False))
    one, _ = await _cast_one(case, source_id, client_a)
    two, _ = await _cast_one(case, source_id, client_b)
    counts = score_recast(one, two, case.schema_)
    # The mentioned machine is written by its key, so the re-cast adds nothing.
    assert counts.recast_vertices == 3
    assert counts.recast_added == 0
    assert counts.recast_minted == 1
    assert counts.recast_duplicated == 1
    assert score_recast(one, one, case.schema_).recast_duplicated == 0


async def test_a_keyless_vertex_matches_by_its_properties(make_client):
    case = BY_NAME["mention/plant"]
    source_id = "mention-plant-2"
    reply = _expected_reply(case, source_id)
    reply["vertices"][0]["quote"] = "the guard is loose again"
    client, _ = make_client(reply)
    outcome, episode = await _cast_one(case, source_id, client)
    counts = score_episode(
        outcome, case.expected[source_id], case.schema_, episode.text
    )
    assert counts.entities_matched == 2
    assert counts.edges_matched == 1
    assert counts.spans_hit == counts.spans_checked - 1


def test_the_command_writes_both_reports(tmp_path: Path):
    result = CliRunner().invoke(
        bench, ["--family", "direction", "--split", "dev", "--out", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    json_files = list(tmp_path.glob("*.json"))
    md_files = list(tmp_path.glob("*.md"))
    assert len(json_files) == 1 and len(md_files) == 1
    data = json.loads(json_files[0].read_text())
    assert data["corpus_version"] == CORPUS_VERSION
    assert [row["label"] for row in data["rows"]] == ["oracle"]
    cases = data["rows"][0]["cases"]
    assert {case["name"] for case in cases} == {
        "direction/helpdesk",
        "direction/logistics",
    }
    assert cases[0]["episodes"][0]["counts"]["episodes"] == 1
    assert "not publishable" in md_files[0].read_text()


def test_the_command_needs_a_row():
    result = CliRunner().invoke(bench, ["--no-oracle"])
    assert result.exit_code != 0
    assert "nothing to run" in result.output


def test_a_replay_miss_stops_the_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SCHEMACAST_LLM_API_KEY", "test-key")
    result = CliRunner().invoke(
        bench,
        [
            "--no-oracle",
            "--model",
            "some-model",
            "--case",
            "direction/plant",
            "--replay",
            "--out",
            str(tmp_path),
        ],
    )
    assert result.exit_code != 0
    assert "cache" in result.output.lower()


def test_a_pick_is_scored_by_what_it_links():
    from graflo.link.records import EntityCandidate

    from schemacast.bench.score import score_picks
    from schemacast.pick import Pick

    spec = BY_NAME["mention/logistics"].picks["mention-logistics-1"]
    kari = EntityCandidate(
        vertex_type="Driver", key={"license": "L-5210"}, lane="relation"
    )
    ola = EntityCandidate(
        vertex_type="Driver", key={"license": "L-4471"}, lane="metadata"
    )

    def _counts(outcome, candidate=None):
        pick = Pick(
            source_id="mention-logistics-1",
            ref="v1",
            type="Driver",
            outcome=outcome,
            candidate=candidate,
        )
        return score_picks([pick], {"v1": "d"}, spec)

    right = _counts("picked", ola)
    assert (right.picks_correct, right.picks_linked, right.picks_wrong) == (1, 1, 0)
    wrong = _counts("picked", kari)
    assert (wrong.picks_correct, wrong.picks_wrong) == (0, 1)
    rejected = _counts("rejected")
    assert (rejected.picks_correct, rejected.picks_rejected) == (0, 1)
    missed = score_picks([], {}, spec)
    assert (missed.picks_expected, missed.picks_offered) == (1, 0)
