"""``schemacast serve``: POST /cast against a scripted model."""

import subprocess
import sys

import pytest
import yaml
from conftest import DATA
from fastapi.testclient import TestClient
from test_cast import ANN, ANN_REPLY, NOTE, NOTE_REPLY

from schemacast.config import SchemaCastSettings, ServeSettings
from schemacast.serve import create_app

KEY = "s3cret"
AUTH = {"Authorization": f"Bearer {KEY}"}
SCHEMA = yaml.safe_load((DATA / "plant.yaml").read_text())


def _body(*episodes, **options):
    return {
        "schema": SCHEMA,
        "episodes": [e.model_dump(mode="json") for e in episodes],
        "options": options,
    }


@pytest.fixture
def make_service(make_client):
    def _build(routes=None, serve=None, **client):
        llm, endpoint = make_client(routes=routes or {}, **client)
        app = create_app(
            SchemaCastSettings(),
            serve or ServeSettings(api_key=KEY),
            client=llm,
        )
        return TestClient(app), endpoint

    return _build


def test_health_needs_no_key(make_service):
    service, _ = make_service()
    body = service.get("/health").json()
    assert body["status"] == "ok" and body["auth_configured"] is True


def test_a_cast_returns_what_the_cli_writes(make_service):
    service, endpoint = make_service({"m1": [ANN_REPLY], "m2": [NOTE_REPLY]})
    response = service.post("/cast", json=_body(ANN, NOTE), headers=AUTH)

    assert response.status_code == 200, response.text
    body = response.json()
    assert [e["status"] for e in body["episodes"]] == ["ok", "ok"]
    assert body["graph"]["data"]["vertices"]["Machine"] == [
        {"serial": "S-7", "power": 200.0}
    ]
    assert body["graph"]["schema"]["metadata"]["name"] == "plant"
    assert {g["kind"] for g in body["groundings"]} == {"vertex", "edge", "property"}
    assert [r["record"] for r in body["quarantine"]] == ["unmapped"]
    assert body["manifest"]["episodes"] == 2
    assert len(endpoint.requests) == 2


def test_a_wrong_or_missing_key_is_refused(make_service):
    service, endpoint = make_service()
    assert service.post("/cast", json=_body(ANN)).status_code == 401
    wrong = {"Authorization": "Bearer nope"}
    assert service.post("/cast", json=_body(ANN), headers=wrong).status_code == 401
    assert endpoint.requests == []


def test_no_configured_key_fails_closed(make_service):
    service, endpoint = make_service(serve=ServeSettings())
    response = service.post("/cast", json=_body(ANN), headers=AUTH)
    assert response.status_code == 503
    assert "SCHEMACAST_SERVE_API_KEY" in response.json()["detail"]
    assert endpoint.requests == []


def test_anonymous_serving_is_explicit(make_service):
    service, _ = make_service(
        {"m1": [ANN_REPLY]}, serve=ServeSettings(allow_anonymous=True)
    )
    assert service.post("/cast", json=_body(ANN)).status_code == 200


def test_the_batch_is_capped(make_service):
    service, endpoint = make_service(serve=ServeSettings(api_key=KEY, max_episodes=1))
    response = service.post("/cast", json=_body(ANN, NOTE), headers=AUTH)
    assert response.status_code == 413
    assert endpoint.requests == []


def test_a_mapping_that_is_no_schema_is_refused(make_service):
    service, _ = make_service()
    body = {**_body(ANN), "schema": {"vertices": []}}
    assert service.post("/cast", json=body, headers=AUTH).status_code == 422


def test_options_reach_the_cast(make_service):
    service, endpoint = make_service({"m1": [ANN_REPLY]})
    body = _body(ANN, user_instruction="Prefer serial numbers.", slice_max_tokens=None)
    assert service.post("/cast", json=body, headers=AUTH).status_code == 200
    assert "Prefer serial numbers." in endpoint.requests[0]["messages"][1]["content"]


def test_a_model_fault_fails_the_episode_not_the_request(make_service):
    from conftest import error

    service, _ = make_service({"m1": [error(429, "Rate limit")], "m2": [NOTE_REPLY]})
    body = service.post("/cast", json=_body(ANN, NOTE), headers=AUTH).json()
    assert [e["failure"] for e in body["episodes"]] == ["provider_error", None]


def test_a_misconfigured_model_is_a_service_error(make_service):
    service, _ = make_service({"m1": [ANN_REPLY]}, api_key=None)
    response = service.post("/cast", json=_body(ANN), headers=AUTH)
    assert response.status_code == 503


def test_importing_the_service_loads_nothing_heavy():
    probe = (
        "import sys, schemacast.serve\n"
        "print(','.join(m for m in ('numpy', 'pandas', 'rdflib', 'torch') "
        "if m in sys.modules))"
    )
    loaded = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    ).stdout.strip()
    assert loaded == ""
