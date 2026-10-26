"""pick: one bounded call links staged mentions to candidates, checked without a model."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient
from graflo.architecture.graph_types.container import GraphContainer
from graflo.architecture.schema.document import Schema
from graflo.connections.graflo_backend import GraFloBackendConfig
from graflo.db.manager import ConnectionManager
from graflo.hq.container_write import write_container
from graflo.link.candidates import gather_candidates
from graflo.link.records import (
    AttributeCheck,
    CandidateHit,
    EntityCandidate,
    PartialRecord,
    RecordGraph,
    RecordRelation,
    StagedCandidates,
    StatedProp,
)
from graflo.link.resolve import promote, resolve_records

from schemacast.cli import main
from schemacast.config import CacheSettings, SchemaCastSettings, ServeSettings
from schemacast.episode import Episode
from schemacast.ground import TextSpan, evidence, quote_of
from schemacast.llm.cache import CacheMissError
from schemacast.pick import (
    PICK_SCHEMA_NAME,
    Picker,
    PickItem,
    apply_picks,
    offers,
    pick_messages,
)
from schemacast.serve import create_app

TEXT = "The customer with email jo@acme.example raised HD-4411."
SPAN = TextSpan(start=0, end=len(TEXT))
RAISED = ("Customer", "Ticket", "RAISED")
SCHEMA: dict[str, Any] = {
    "metadata": {"name": "support", "version": "1.0.0"},
    "core_schema": {
        "vertex_config": {
            "vertices": [
                {
                    "name": "Customer",
                    "properties": [
                        "account",
                        "email",
                        {
                            "name": "name",
                            "semantics": {"iri": "https://schema.org/name"},
                        },
                    ],
                    "identity": ["account"],
                    "secondary_identities": [{"name": "by_email", "fields": ["email"]}],
                },
                {"name": "Agent", "properties": ["handle"], "identity": ["handle"]},
                {"name": "Ticket", "properties": ["number"], "identity": ["number"]},
            ]
        },
        "edge_config": {
            "edges": [{"source": "Customer", "target": "Ticket", "relation": "RAISED"}]
        },
    },
}


def _prop(value: Any) -> StatedProp:
    return StatedProp(value=value, evidence=evidence(str(value), SPAN))


def _graph() -> RecordGraph:
    return RecordGraph(
        source_id="mail-1",
        schema_ref="support@1.0.0",
        records=[
            PartialRecord(
                ref="c",
                type="Customer",
                may_create=False,
                props={"email": _prop("jo@acme.example")},
                evidence=evidence("The customer with email jo@acme.example", SPAN),
            ),
            PartialRecord(
                ref="t",
                type="Ticket",
                may_create=False,
                props={"number": _prop("HD-4411")},
                evidence=evidence("HD-4411", SPAN),
            ),
        ],
        relations=[
            RecordRelation(
                edge_id=RAISED, source="c", target="t", evidence=evidence(TEXT, SPAN)
            )
        ],
    )


EPISODE = Episode(source_id="mail-1", text=TEXT)
JO = EntityCandidate(
    vertex_type="Customer",
    key={"account": "ACC-31"},
    label="Jo Andersson",
    lanes=[CandidateHit(lane="relation", relation="RAISED", anchor="t")],
)
PRIYA = EntityCandidate(
    vertex_type="Customer",
    key={"account": "ACC-12"},
    label="Priya Nair",
    lanes=[CandidateHit(lane="metadata", role="speaker")],
)
KIM = EntityCandidate(
    vertex_type="Agent",
    key={"handle": "kim"},
    lanes=[CandidateHit(lane="metadata", role="speaker")],
)


def _item(*candidates: EntityCandidate, entities=(), graph=None) -> PickItem:
    record = _graph().records[0]
    return PickItem(
        episode=EPISODE,
        staged=[
            StagedCandidates(
                source_id="mail-1",
                record=record,
                reason="unmatched",
                candidates=list(candidates),
            )
        ],
        entities=list(entities),
        graph=graph,
    )


def _reply(*pairs: tuple[str, str]) -> dict[str, Any]:
    return {"picks": [{"mention": m, "candidate": c} for m, c in pairs]}


def _pick(make_client, items, *replies, **kwargs):
    client, endpoint = make_client(*replies, **kwargs)
    result = asyncio.run(Picker(client=client).pick(items))
    return result, endpoint


# -- the whole round: cast mentions, core candidates, pick, re-resolution ---


def _schema() -> Schema:
    return Schema.model_validate(SCHEMA)


def test_a_picked_mention_resolves_to_its_candidates_key(make_client, tmp_path):
    conf = GraFloBackendConfig(output_dir=tmp_path)
    asyncio.run(
        write_container(
            _schema(),
            GraphContainer(
                vertices={
                    "Customer": [{"account": "ACC-31", "name": "Jo Andersson"}],
                    "Ticket": [{"number": "HD-4411"}],
                },
                edges={RAISED: [({"account": "ACC-31"}, {"number": "HD-4411"}, {})]},
            ),
            conf,
        )
    )
    graph = _graph()
    with ConnectionManager(connection_config=conf) as db:
        first = resolve_records(_schema(), [graph], db=db)
        staged = gather_candidates(
            _schema(), first, db, lookup_text=lambda r: quote_of(r.evidence)
        )
    assert [s.reason for s in staged] == ["unmatched"]
    assert [c.key for c in staged[0].candidates] == [{"account": "ACC-31"}]

    item = PickItem(episode=EPISODE, staged=staged, graph=graph)
    result, _ = _pick(make_client, [item], _reply(("m1", "c1")))
    (pick,) = result.picks
    assert (pick.ref, pick.outcome, pick.key) == ("c", "picked", {"account": "ACC-31"})

    with ConnectionManager(connection_config=conf) as db:
        again = resolve_records(_schema(), apply_picks([graph], result.picks), db=db)
    assert again.staged.records == [] and again.staged.relations == []
    assert again.identity_of("mail-1", "c") == {"account": "ACC-31"}
    # A pick links: the mention's stated email is not written onto the entity.
    container = promote(_schema(), again)
    assert container.vertices["Customer"] == [{"account": "ACC-31"}]
    assert container.edges[RAISED] == [
        ({"account": "ACC-31"}, {"number": "HD-4411"}, {})
    ]


# -- the call ----------------------------------------------------------------


def test_one_call_offers_ids_as_enums_and_text_as_data(make_client):
    result, endpoint = _pick(
        make_client,
        [_item(JO, entities=[PRIYA, KIM], graph=_graph())],
        _reply(("m1", "c1")),
    )
    assert result.picks[0].outcome == "picked"
    (request,) = endpoint.requests
    shape = request["response_format"]["json_schema"]
    assert shape["name"] == PICK_SCHEMA_NAME
    item = shape["schema"]["properties"]["picks"]["items"]["properties"]
    assert item["mention"]["enum"] == ["m1"]
    # The agent is not a customer, so it is not offered.
    assert item["candidate"]["enum"] == ["c1", "c2", "none"]
    user = request["messages"][1]["content"]
    assert "<<<EPISODE id=mail-1" in user and "<<<MENTIONS id=mail-1" in user
    assert '"Jo Andersson" (Customer; account="ACC-31")' in user
    assert 'relation: linked by RAISED to "HD-4411"' in user
    assert "metadata: the record's speaker" in user
    assert "kim" not in user


def test_the_prompt_is_byte_deterministic():
    item = _item(JO, entities=[PRIYA])
    assert pick_messages(item, offers(item)) == pick_messages(item, offers(item))


# -- evidence ----------------------------------------------------------------

JO_BY_NAME = EntityCandidate(
    vertex_type="Customer",
    key={"account": "ACC-31"},
    label="Jo Andersson",
    lanes=[CandidateHit(lane="exact", matched="Jo Andersson")],
)


def _mentions(item: PickItem) -> list[str]:
    user = pick_messages(item, offers(item))[1]["content"]
    return [line for line in user.splitlines() if line.startswith("  c")]


def test_one_lane_and_no_attributes_render_as_one_way_found():
    assert _mentions(
        _item(
            JO_BY_NAME,
            JO.model_copy(update={"key": {"account": "ACC-7"}}),
            graph=_graph(),
        )
    ) == [
        '  c1: "Jo Andersson" (Customer; account="ACC-31"); found by exact: "Jo Andersson"',
        '  c2: "Jo Andersson" (Customer; account="ACC-7"); found by relation: linked by RAISED to "HD-4411"',
    ]


def test_every_lane_and_the_attribute_verdicts_are_shown():
    jo = JO_BY_NAME.model_copy(
        update={
            "lanes": [*JO_BY_NAME.lanes, *JO.lanes],
            "attributes": [
                AttributeCheck(field="name", verdict="agree", compare="literal"),
                AttributeCheck(field="email", verdict="contradict", compare="literal"),
                AttributeCheck(
                    field="name_norm", verdict="unknown", compare="normalized"
                ),
            ],
        }
    )
    (line,) = _mentions(_item(jo, graph=_graph()))
    assert line == (
        '  c1: "Jo Andersson" (Customer; account="ACC-31"); found by exact: '
        '"Jo Andersson", relation: linked by RAISED to "HD-4411"; stated name '
        "agrees, email contradicts, name_norm unknown (normalized)"
    )


def test_a_metadata_entity_already_a_candidate_adds_its_lane():
    priya = JO_BY_NAME.model_copy(update={"key": PRIYA.key, "label": None})
    (offer,) = offers(_item(priya, entities=[PRIYA]))
    (candidate,) = offer.candidates.values()
    assert [hit.lane for hit in candidate.lanes] == ["exact", "metadata"]
    assert candidate.label == "Priya Nair"


def test_the_check_ignores_evidence_against_a_candidate(make_client):
    against = JO.model_copy(
        update={
            "attributes": [
                AttributeCheck(field="email", verdict="contradict", compare="literal")
            ]
        }
    )
    result, _ = _pick(make_client, [_item(against)], _reply(("m1", "c1")))
    assert [(p.outcome, p.key) for p in result.picks] == [
        ("picked", {"account": "ACC-31"})
    ]


def test_none_and_an_unanswered_mention_link_nothing(make_client):
    result, _ = _pick(make_client, [_item(JO)], _reply(("m1", "none")))
    assert [(p.outcome, p.candidate) for p in result.picks] == [("none", None)]
    # The same request again would be a cache hit.
    off = CacheSettings(enabled=False)
    result, _ = _pick(make_client, [_item(JO)], _reply(), cache=off)
    assert [(p.outcome, p.detail) for p in result.picks] == [("none", "not answered")]


def test_a_candidate_listed_for_another_mention_is_rejected(make_client):
    item = _item(JO)
    other = item.staged[0].model_copy(
        update={
            "record": item.staged[0].record.model_copy(update={"ref": "c2"}),
            "candidates": [PRIYA],
        }
    )
    item = item.model_copy(update={"staged": [*item.staged, other]})
    result, _ = _pick(make_client, [item], _reply(("m1", "c2"), ("m2", "c2")))
    assert [(p.ref, p.outcome) for p in result.picks] == [
        ("c", "rejected"),
        ("c2", "picked"),
    ]
    assert result.picks[0].detail == "c2 is not listed for it"
    assert result.manifest.rejected == 1


def test_two_answers_for_one_mention_are_rejected(make_client):
    result, _ = _pick(
        make_client, [_item(JO, PRIYA)], _reply(("m1", "c1"), ("m1", "c2"))
    )
    assert result.picks[0].outcome == "rejected"


def test_a_mention_with_nothing_to_choose_makes_no_call(make_client):
    result, endpoint = _pick(make_client, [_item(entities=[KIM])])
    assert [p.outcome for p in result.picks] == ["no_candidates"]
    assert [e.status for e in result.episodes] == ["skipped"]
    assert endpoint.requests == []


def test_a_provider_failure_fails_the_episode_and_the_run_goes_on(make_client):
    from conftest import error

    first = Episode(source_id="a", text="x")
    second = Episode(source_id="b", text="y")
    items = [
        _item(JO).model_copy(update={"episode": first}),
        _item(JO).model_copy(update={"episode": second}),
    ]
    result, _ = _pick(
        make_client,
        items,
        routes={"a": [error(500, "boom")], "b": [_reply(("m1", "c1"))]},
    )
    assert [e.status for e in result.episodes] == ["failed", "ok"]
    assert [p.outcome for p in result.picks] == ["failed", "picked"]
    assert result.episodes[0].failure == "provider_error"


def test_a_pick_replays_from_the_cache(make_client):
    _pick(make_client, [_item(JO)], _reply(("m1", "c1")))
    replay = CacheSettings(require_hit=True)
    result, endpoint = _pick(make_client, [_item(JO)], cache=replay)
    assert result.picks[0].outcome == "picked"
    assert endpoint.requests == []
    assert result.manifest.usage.cache_hits == 1
    with pytest.raises(CacheMissError):
        _pick(make_client, [_item(JO, PRIYA)], cache=replay)


def test_an_item_holds_one_source():
    item = _item(JO)
    other = item.staged[0].model_copy(update={"source_id": "mail-2"})
    with pytest.raises(ValueError, match="another source"):
        PickItem(episode=EPISODE, staged=[other])


def test_apply_picks_leaves_other_mentions_alone():
    graph = _graph()
    assert apply_picks([graph], []) == [graph]


# -- service and command line ------------------------------------------------

KEY = "s3cret"
AUTH = {"Authorization": f"Bearer {KEY}"}


@pytest.fixture
def service(make_client):
    def _build(*replies, serve=None):
        client, endpoint = make_client(*replies)
        app = create_app(
            SchemaCastSettings(), serve or ServeSettings(api_key=KEY), client=client
        )
        return TestClient(app), endpoint

    return _build


def test_post_pick_returns_one_pick_per_staged_mention(service):
    client, _ = service(_reply(("m1", "c1")))
    body = {"episodes": [_item(JO).model_dump(mode="json")]}
    response = client.post("/pick", json=body, headers=AUTH)
    assert response.status_code == 200, response.text
    data = response.json()
    assert [(p["ref"], p["outcome"]) for p in data["picks"]] == [("c", "picked")]
    assert data["picks"][0]["candidate"]["key"] == {"account": "ACC-31"}
    assert data["episodes"][0]["status"] == "ok"
    assert data["manifest"]["picked"] == 1


def test_post_pick_needs_the_key_and_a_bounded_batch(service):
    client, endpoint = service(serve=ServeSettings(api_key=KEY, max_episodes=1))
    body = {"episodes": [_item(JO).model_dump(mode="json")]}
    assert client.post("/pick", json=body).status_code == 401
    body["episodes"] *= 2
    assert client.post("/pick", json=body, headers=AUTH).status_code == 413
    assert endpoint.requests == []


def test_the_cli_picks_and_replays(make_client, tmp_path: Path, monkeypatch):
    client, _ = make_client(_reply(("m1", "c1")))
    asyncio.run(Picker(client=client).pick([_item(JO)]))
    source = tmp_path / "pick.jsonl"
    source.write_text(_item(JO).model_dump_json() + "\n")
    monkeypatch.setenv("SCHEMACAST_LLM_MODEL", client.settings.model)
    monkeypatch.setenv("SCHEMACAST_LLM_BASE_URL", client.settings.base_url or "")
    out = tmp_path / "out"

    result = CliRunner().invoke(
        main, ["pick", "--input", str(source), "--out", str(out), "--replay"]
    )

    assert result.exit_code == 0, result.output
    assert "1 picked, 0 none, 0 rejected" in result.output
    (line,) = (out / "picks.jsonl").read_text().splitlines()
    assert json.loads(line)["candidate"]["key"] == {"account": "ACC-31"}
    assert json.loads((out / "manifest.json").read_text())["usage"]["calls"] == 0


def test_the_cli_replay_miss_is_a_clean_error(tmp_path: Path, monkeypatch):
    source = tmp_path / "pick.jsonl"
    source.write_text(_item(JO).model_dump_json() + "\n")
    monkeypatch.setenv("SCHEMACAST_LLM_MODEL", "fake-model")
    result = CliRunner().invoke(
        main, ["pick", "--input", str(source), "--out", str(tmp_path), "--replay"]
    )
    assert result.exit_code == 1
    assert "require hits" in result.output
