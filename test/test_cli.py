"""The schemacast command line."""

import json

from click.testing import CliRunner
from conftest import DATA

from schemacast.cli import main


def test_sheet_prints_the_type_sheet():
    result = CliRunner().invoke(main, ["sheet", "--schema", str(DATA / "schema.yaml")])
    assert result.exit_code == 0, result.output
    assert (
        'Machine  "A production machine."  ~ unit; station  id: serial' in result.output
    )
    assert "power FLOAT [kW]" in result.output
    assert result.output.rstrip().endswith("does not exist.")


def test_sheet_reads_a_manifest():
    result = CliRunner().invoke(
        main, ["sheet", "--schema", str(DATA / "manifest.yaml")]
    )
    assert result.exit_code == 0, result.output
    assert "Machine  id: serial" in result.output


def test_sheet_prints_the_reply_schema():
    result = CliRunner().invoke(
        main, ["sheet", "--schema", str(DATA / "schema.yaml"), "--json-schema"]
    )
    assert result.exit_code == 0, result.output
    shape = json.loads(result.output)
    vertex = shape["properties"]["vertices"]["items"]
    assert vertex["properties"]["type"]["enum"] == ["Machine", "Person", "WorkOrder"]


def test_select_prints_one_json_line_per_episode():
    result = CliRunner().invoke(
        main,
        [
            "select",
            "--schema",
            str(DATA / "schema.yaml"),
            "--episodes",
            str(DATA / "episodes.jsonl"),
        ],
    )
    assert result.exit_code == 0, result.output
    records = [json.loads(line) for line in result.output.splitlines()]
    assert [r["source_id"] for r in records] == ["msg-1", "msg-2"]
    assert records[0]["mode"] == "whole"
    assert records[0]["seeds"][0]["seed"] == "Machine"


def test_select_reports_a_failure_and_exits_non_zero(monkeypatch):
    monkeypatch.setenv("SCHEMACAST_SLICE_MAX_TOKENS", "10")
    result = CliRunner().invoke(
        main, ["select", "--schema", str(DATA / "schema.yaml"), "--text", "zzz"]
    )
    assert result.exit_code == 1
    assert json.loads(result.output)["error"] == "no_match"


def test_select_needs_exactly_one_input():
    result = CliRunner().invoke(main, ["select", "--schema", str(DATA / "schema.yaml")])
    assert result.exit_code == 2


def test_cast_replays_saves_and_writes(make_client, tmp_path, monkeypatch):
    import asyncio

    from test_cast import ANN, ANN_REPLY, NOTE, NOTE_REPLY

    from schemacast.cast import SchemaCaster
    from schemacast.schema_io import load_schema

    plant = DATA / "plant.yaml"
    client, _ = make_client(routes={"m1": [ANN_REPLY], "m2": [NOTE_REPLY]})
    asyncio.run(SchemaCaster(load_schema(plant), client=client).cast([ANN, NOTE]))

    episodes = tmp_path / "episodes.jsonl"
    episodes.write_text(
        "".join(e.model_dump_json(exclude_none=True) + "\n" for e in (ANN, NOTE))
    )
    conn = tmp_path / "conn.yaml"
    conn.write_text(f"db_type: graflo_backend\noutput_dir: {tmp_path / 'db'}\n")
    monkeypatch.setenv("SCHEMACAST_LLM_MODEL", client.settings.model)
    monkeypatch.setenv("SCHEMACAST_LLM_BASE_URL", client.settings.base_url or "")
    out = tmp_path / "out"

    result = CliRunner().invoke(
        main,
        [
            "cast",
            "--schema",
            str(plant),
            "--episodes",
            str(episodes),
            "--out",
            str(out),
            "--db",
            str(conn),
            "--replay",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "2 episodes (2 ok, 0 partial, 0 failed)" in result.output
    for name in (
        "graph.json",
        "groundings.jsonl",
        "episodes.jsonl",
        "manifest.json",
        "quarantine.jsonl",
        "write.json",
    ):
        assert (out / name).exists(), name
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["usage"]["calls"] == 0
    assert manifest["usage"]["cache_hits"] == 2
    assert json.loads((out / "write.json").read_text())["vertices"]["Machine"] == 1


def test_cast_replay_miss_is_a_clean_error(tmp_path, monkeypatch):
    episodes = tmp_path / "episodes.jsonl"
    episodes.write_text('{"source_id": "x", "text": "press S-7 at 200 kW"}\n')
    monkeypatch.setenv("SCHEMACAST_LLM_MODEL", "fake-model")
    result = CliRunner().invoke(
        main,
        [
            "cast",
            "--schema",
            str(DATA / "plant.yaml"),
            "--episodes",
            str(episodes),
            "--out",
            str(tmp_path / "out"),
            "--replay",
        ],
    )
    assert result.exit_code == 1
    assert "require hits" in result.output


def _cast(make_client, routes):
    import asyncio

    from test_cast import ANN, NOTE

    from schemacast.cast import SchemaCaster
    from schemacast.schema_io import load_schema

    client, _ = make_client(routes=routes)
    caster = SchemaCaster(load_schema(DATA / "plant.yaml"), client=client)
    return asyncio.run(caster.cast([ANN, NOTE]))


def test_a_failure_on_the_content_exits_zero(make_client, tmp_path):
    from test_cast import NOTE_REPLY

    from schemacast.cli import cast_summary

    result = _cast(make_client, {"m1": ["not json"] * 3, "m2": [NOTE_REPLY]})
    lines, status = cast_summary(result, tmp_path, cache_enabled=True)
    assert status == 0
    assert "1 failed: unparseable 1" in lines[0]
    assert len(lines) == 1


def test_a_provider_failure_exits_75_and_says_to_rerun(make_client, tmp_path):
    from conftest import error
    from test_cast import NOTE_REPLY

    from schemacast.cli import EX_TEMPFAIL, cast_summary

    result = _cast(make_client, {"m1": [error(500, "down")], "m2": [NOTE_REPLY]})
    lines, status = cast_summary(result, tmp_path, cache_enabled=True)
    assert status == EX_TEMPFAIL
    assert "1 failed: provider_error 1" in lines[0]
    assert "rerun the same command" in lines[1]
    assert "cached replies cost nothing" in lines[1]


def test_every_episode_failing_exits_one(make_client, tmp_path):
    from conftest import error

    from schemacast.cli import cast_summary

    down = [error(500, "down")]
    result = _cast(make_client, {"m1": down, "m2": down})
    _, status = cast_summary(result, tmp_path, cache_enabled=True)
    assert status == 1


def test_cast_against_an_unreachable_endpoint_stops_at_once(tmp_path, monkeypatch):
    episodes = tmp_path / "episodes.jsonl"
    episodes.write_text('{"source_id": "x", "text": "press S-7 at 200 kW"}\n')
    # Port 9 on loopback refuses the connection; nothing leaves the machine.
    monkeypatch.setenv("SCHEMACAST_LLM_BASE_URL", "http://127.0.0.1:9/v1")
    monkeypatch.setenv("SCHEMACAST_LLM_MODEL", "fake-model")
    monkeypatch.setenv("SCHEMACAST_LLM_API_KEY", "test-key")
    monkeypatch.setenv("SCHEMACAST_LLM_TRANSPORT_RETRIES", "0")
    monkeypatch.setenv("SCHEMACAST_CACHE_ENABLED", "false")
    result = CliRunner().invoke(
        main,
        [
            "cast",
            "--schema",
            str(DATA / "plant.yaml"),
            "--episodes",
            str(episodes),
            "--out",
            str(tmp_path / "out"),
        ],
    )
    assert result.exit_code == 1
    assert "Cannot reach http://127.0.0.1:9/v1" in result.output
