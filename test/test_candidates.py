"""Known entities: candidates from the graph, picked in the cast, checked without a model."""

import json
from typing import Any

import pytest
from conftest import DATA
from pydantic import ValidationError

from schemacast.cast import OUTPUT_FORMAT_VERSION, SchemaCaster
from schemacast.episode import Candidate, Episode
from schemacast.extract import WireExtraction, with_candidates
from schemacast.output import UNRESOLVED_FILE, save_cast
from schemacast.prompt import build_messages
from schemacast.resolve import resolve
from schemacast.schema_io import load_schema
from schemacast.sink import MemoryQuarantineSink
from schemacast.validate import validate

TEXT = "The big press is down again; Ann's ticket from Tuesday mentions it."
PRESS = Candidate(
    cid="k1",
    type="Machine",
    key={"serial": "S-7"},
    label="the big press",
    lane="alias",
)
ANN = Candidate(cid="k2", type="Person", key={"email": "ann@x.com"}, label="Ann")
EPISODE = Episode(source_id="m1", text=TEXT, candidates=[PRESS, ANN])


def _vertex(ref: str, type_: str, quote: str, **extra: Any) -> dict[str, Any]:
    return {
        "ref": ref,
        "type": type_,
        "quote": quote,
        "mention": True,
        "props": [],
        **extra,
    }


PICKED: dict[str, Any] = {
    "vertices": [
        _vertex("v1", "Machine", "The big press", candidate="k1"),
        _vertex("v2", "WorkOrder", "Ann's ticket from Tuesday"),
    ],
    "edges": [
        {
            "relation": "CONCERNS",
            "from": "v2",
            "to": "v1",
            "quote": "Ann's ticket from Tuesday mentions it",
            "props": [],
        }
    ],
    "unmapped": [],
}


@pytest.fixture
def plant():
    return load_schema(DATA / "plant.yaml")


def _check(plant, reply, episode=EPISODE):
    return validate(
        WireExtraction.model_validate(reply),
        plant,
        episode.text,
        episode.context,
        episode.candidates,
    )


class TestEpisode:
    def test_candidate_ids_are_unique(self) -> None:
        with pytest.raises(ValidationError, match="k1"):
            Episode(source_id="m", text="x", candidates=[PRESS, PRESS])

    def test_an_episode_without_candidates_is_unchanged(self) -> None:
        assert Episode(source_id="m", text="x").candidates == []


class TestPrompt:
    def test_candidates_are_listed_with_their_ids(self, plant) -> None:
        user = build_messages(plant, EPISODE)[1]["content"]
        assert "# KNOWN ENTITIES" in user
        assert "k1" in user and "the big press" in user and "S-7" in user
        assert user.index("# KNOWN ENTITIES") < user.index("# TEXT")

    def test_without_candidates_the_prompt_has_no_section(self, plant) -> None:
        plain = Episode(source_id="m1", text=TEXT)
        messages = build_messages(plant, plain)
        assert "KNOWN ENTITIES" not in messages[0]["content"]
        assert "KNOWN ENTITIES" not in messages[1]["content"]
        assert "candidate" not in messages[0]["content"]


class TestWireSchema:
    def test_candidate_is_an_enum_of_the_episode_ids(self) -> None:
        shape = {
            "type": "object",
            "properties": {
                "vertices": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"ref": {"type": "string"}},
                        "required": ["ref"],
                        "additionalProperties": False,
                    },
                }
            },
        }
        extended = with_candidates(shape, ["k1", "k2"])
        vertex = extended["properties"]["vertices"]["items"]
        assert vertex["properties"]["candidate"] == {
            "type": ["string", "null"],
            "enum": ["k1", "k2", None],
        }
        assert "candidate" in vertex["required"]
        assert "candidate" not in shape["properties"]["vertices"]["items"]["properties"]

    def test_no_candidates_leaves_the_schema_alone(self) -> None:
        shape = {"type": "object", "properties": {}}
        assert with_candidates(shape, []) is shape


class TestValidate:
    def test_a_pick_identifies_a_mention_without_a_key(self, plant) -> None:
        result = _check(plant, PICKED)
        (press,) = [v for v in result.vertices if v.ref == "v1"]
        assert press.candidate == "k1"
        assert press.candidate_key == {"serial": "S-7"}
        assert result.findings == []

    def test_a_key_less_mention_without_a_pick_is_unresolved(self, plant) -> None:
        result = _check(plant, PICKED)
        assert [(u.ref, u.type) for u in result.unresolved] == [("v2", "WorkOrder")]
        assert result.unresolved[0].held_edges == 1
        assert result.edges == []

    def test_a_pick_of_the_wrong_type_is_rejected(self, plant) -> None:
        reply = {
            "vertices": [_vertex("v1", "Machine", "The big press", candidate="k2")]
        }
        result = _check(plant, reply)
        assert [f.kind for f in result.fixes] == ["candidate_rejected"]
        assert result.vertices == []
        assert [u.ref for u in result.unresolved] == ["v1"]

    def test_an_unknown_pick_is_rejected(self, plant) -> None:
        reply = {
            "vertices": [_vertex("v1", "Machine", "The big press", candidate="k9")]
        }
        result = _check(plant, reply)
        assert [f.kind for f in result.fixes] == ["candidate_rejected"]

    def test_a_stated_key_still_resolves_without_a_pick(self, plant) -> None:
        text = "Ann (ann@x.com) is here."
        reply = {
            "vertices": [
                _vertex(
                    "v1",
                    "Person",
                    "Ann (ann@x.com)",
                    props=[
                        {"name": "email", "value": "ann@x.com", "quote": "ann@x.com"}
                    ],
                )
            ]
        }
        episode = Episode(source_id="m", text=text)
        result = _check(plant, reply, episode)
        assert [v.ref for v in result.vertices] == ["v1"] and result.unresolved == []


class TestResolve:
    def test_a_pick_writes_by_the_candidate_key(self, plant) -> None:
        validation = _check(plant, PICKED)
        resolution = resolve(validation, plant, EPISODE, extractor="test")
        vertex_groundings = [g for g in resolution.groundings if g.kind == "vertex"]
        assert [(g.identity, g.resolved_by, g.lane) for g in vertex_groundings] == [
            ({"serial": "S-7"}, "candidate", "alias")
        ]

    def test_resolved_by_names_how_each_vertex_was_identified(self, plant) -> None:
        text = "Ann (ann@x.com) and +1 555 opened a work order for press S-7."
        reply = {
            "vertices": [
                _vertex(
                    "a",
                    "Person",
                    "Ann (ann@x.com)",
                    props=[
                        {"name": "email", "value": "ann@x.com", "quote": "ann@x.com"}
                    ],
                ),
                _vertex(
                    "b",
                    "Person",
                    "+1 555",
                    props=[{"name": "phone", "value": "+1 555", "quote": "+1 555"}],
                ),
                {
                    "ref": "w",
                    "type": "WorkOrder",
                    "quote": "a work order",
                    "mention": False,
                    "props": [],
                },
            ]
        }
        episode = Episode(source_id="m", text=text)
        validation = _check(plant, reply, episode)
        resolution = resolve(validation, plant, episode, extractor="test")
        kinds = sorted(
            str(g.resolved_by) for g in resolution.groundings if g.kind == "vertex"
        )
        assert kinds == ["identity", "minted", "secondary"]


@pytest.fixture
def make_caster(make_client, plant):
    def _build(routes, **kwargs):
        client, endpoint = make_client(routes=routes)
        sink = MemoryQuarantineSink()
        return SchemaCaster(plant, client=client, sink=sink, **kwargs), endpoint

    return _build


class TestCast:
    async def test_a_pick_links_and_an_unresolved_mention_is_reported(
        self, make_caster
    ) -> None:
        caster, endpoint = make_caster({"m1": [PICKED]})
        result = await caster.cast([EPISODE])

        (episode,) = result.episodes
        assert episode.unresolved == 1
        assert episode.repairs == 0 and len(endpoint.requests) == 1
        assert [(u.source_id, u.ref, u.type) for u in result.unresolved] == [
            ("m1", "v2", "WorkOrder")
        ]
        assert result.manifest.unresolved == 1
        sent = endpoint.requests[0]["response_format"]["json_schema"]["schema"]
        vertex = sent["properties"]["vertices"]["items"]
        assert vertex["properties"]["candidate"]["enum"] == ["k1", "k2", None]

    async def test_unresolved_mentions_are_saved(self, make_caster, tmp_path) -> None:
        caster, _ = make_caster({"m1": [PICKED]})
        result = await caster.cast([EPISODE])
        save_cast(result, tmp_path)
        lines = (tmp_path / UNRESOLVED_FILE).read_text().splitlines()
        assert [json.loads(line)["ref"] for line in lines] == ["v2"]
        manifest = json.loads((tmp_path / "manifest.json").read_text())
        assert manifest["output_format_version"] == OUTPUT_FORMAT_VERSION == 3


class TestKnownFile:
    def test_candidates_are_read_and_given_to_bare_episodes(self, tmp_path) -> None:
        from schemacast.episode import read_candidates, with_known

        path = tmp_path / "known.jsonl"
        path.write_text(
            "\n".join(c.model_dump_json() for c in (PRESS, ANN)) + "\n",
            encoding="utf-8",
        )
        known = read_candidates(path)
        assert [c.cid for c in known] == ["k1", "k2"]
        bare = Episode(source_id="b", text="x")
        own = Episode(source_id="o", text="x", candidates=[ANN])
        out = list(with_known([bare, own], known))
        assert [len(e.candidates) for e in out] == [2, 1]

    def test_a_repeated_cid_is_refused(self, tmp_path) -> None:
        from schemacast.episode import EpisodeFileError, read_candidates

        path = tmp_path / "known.jsonl"
        path.write_text(PRESS.model_dump_json() + "\n" + PRESS.model_dump_json() + "\n")
        with pytest.raises(EpisodeFileError, match="k1"):
            read_candidates(path)


def test_the_service_takes_candidates_and_returns_unresolved(make_client) -> None:
    import yaml
    from fastapi.testclient import TestClient

    from schemacast.config import SchemaCastSettings, ServeSettings
    from schemacast.serve import create_app

    llm, _ = make_client(routes={"m1": [PICKED]})
    app = create_app(SchemaCastSettings(), ServeSettings(api_key="k"), client=llm)
    body = {
        "schema": yaml.safe_load((DATA / "plant.yaml").read_text()),
        "episodes": [EPISODE.model_dump(mode="json")],
        "options": {},
    }
    response = TestClient(app).post(
        "/cast", json=body, headers={"Authorization": "Bearer k"}
    )
    assert response.status_code == 200, response.text
    reply = response.json()
    assert [u["ref"] for u in reply["unresolved"]] == ["v2"]
    vertex = [g for g in reply["groundings"] if g["kind"] == "vertex"]
    assert [g["resolved_by"] for g in vertex] == ["candidate"]
