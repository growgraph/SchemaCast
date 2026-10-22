"""The bench on a corpus of your own, and scoring saved casts without a model."""

import asyncio
import json
import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml
from click.testing import CliRunner

from schemacast.bench.cli import main as bench
from schemacast.bench.corpus import (
    BENCH_DIR,
    CORPUS_DIGEST,
    ExpectedEpisode,
    load_corpus,
)
from schemacast.bench.oracle import oracle_client, oracle_reply
from schemacast.bench.report import case_score
from schemacast.bench.run import case_settings
from schemacast.bench.score import Misses, score_episode
from schemacast.cast import SchemaCaster
from schemacast.config import SchemaCastSettings

BY_NAME = {case.name: case for case in load_corpus()}
CASES = ["direction/helpdesk", "units/lab", "identity/plant", "known/lab"]


def _own_corpus(root: Path, names: list[str]) -> Path:
    """A corpus outside the package holding copies of *names*, all in ``dev``."""
    schemas: set[str] = set()
    for name in names:
        shutil.copytree(BENCH_DIR / "cases" / name, root / "cases" / name)
        meta = yaml.safe_load((BENCH_DIR / "cases" / name / "case.yaml").read_text())
        parts = meta["schema"]
        schemas.update([parts] if isinstance(parts, str) else parts)
    (root / "schemas").mkdir()
    for part in schemas:
        shutil.copy(
            BENCH_DIR / "schemas" / f"{part}.yaml", root / "schemas" / f"{part}.yaml"
        )
    (root / "cases" / "SPLITS.yaml").write_text(yaml.safe_dump({"dev": names}))
    return root


def _report(out: Path) -> dict[str, Any]:
    (path,) = out.glob("*.json")
    return json.loads(path.read_text())


def _saved_cast(name: str, path: Path, drop: str | None = None) -> Path:
    """The oracle's cast of case *name*, saved as a ``POST /cast`` body."""
    case = BY_NAME[name]
    caster = SchemaCaster(
        case.schema_,
        settings=case_settings(case, SchemaCastSettings()),
        client=oracle_client(case.expected),
    )
    episodes = [e for e in case.episodes if e.source_id != drop]
    result = asyncio.run(caster.cast(episodes))
    body = {
        "mentions": [g.model_dump(mode="json") for g in result.graphs],
        "graph": result.output.to_dict(),
        "staged": result.resolution.staged.model_dump(mode="json"),
        "groundings": [g.model_dump(mode="json") for g in result.groundings],
        "unmapped": {},
        "quarantine": [],
        "episodes": [e.model_dump(mode="json") for e in result.episodes],
        "manifest": result.manifest.model_dump(mode="json"),
    }
    path.write_text(json.dumps(body))
    return path


def test_a_corpus_of_your_own_runs_the_oracle_row(tmp_path: Path):
    root = _own_corpus(tmp_path / "corpus", CASES)
    out = tmp_path / "out"
    result = CliRunner().invoke(bench, ["--corpus", str(root), "--out", str(out)])
    assert result.exit_code == 0, result.output
    data = _report(out)
    assert data["corpus_dir"] == str(root)
    assert data["corpus_version"] == "external"
    (row,) = data["rows"]
    assert sorted(case["name"] for case in row["cases"]) == sorted(CASES)
    assert all(case["score"] == 1.0 for case in row["cases"])
    assert "a corpus outside the package" in result.output


def test_a_corpus_of_your_own_is_never_publishable(tmp_path: Path):
    # A copy of the bundled corpus has the pinned digest, and is still outside.
    root = tmp_path / "corpus"
    shutil.copytree(BENCH_DIR / "cases", root / "cases")
    shutil.copytree(BENCH_DIR / "schemas", root / "schemas")
    out = tmp_path / "out"
    result = CliRunner().invoke(
        bench,
        [
            "--corpus",
            str(root),
            "--split",
            "test",
            "--family",
            "direction",
            "--out",
            str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    data = _report(out)
    assert data["corpus_digest"] == CORPUS_DIGEST
    assert data["corpus_modified"] is False
    assert "**not publishable**" in result.output


def test_a_directory_without_the_layout_is_refused(tmp_path: Path):
    result = CliRunner().invoke(bench, ["--corpus", str(tmp_path)])
    assert result.exit_code != 0
    assert "SPLITS.yaml" in result.output


def test_scoring_the_oracles_saved_casts_equals_its_run(tmp_path: Path):
    run_out, score_out = tmp_path / "run", tmp_path / "score"
    ran = CliRunner().invoke(
        bench, [*(f"--case={n}" for n in CASES), "--out", str(run_out)]
    )
    assert ran.exit_code == 0, ran.output
    casts = [
        _saved_cast(name, tmp_path / f"cast-{i}.json") for i, name in enumerate(CASES)
    ]
    # One cast in the wrapper a service caller keeps.
    body = json.loads(casts[0].read_text())
    casts[0].write_text(json.dumps({"episodes": [], "cast": body}))
    scored = CliRunner().invoke(
        bench,
        [
            "score",
            *map(str, casts),
            *(f"--case={n}" for n in CASES),
            "--out",
            str(score_out),
        ],
    )
    assert scored.exit_code == 0, scored.output
    (run_row,) = _report(run_out)["rows"]
    (score_row,) = _report(score_out)["rows"]
    assert score_row["label"] == "oracle" and score_row["saved"] is True
    keys = (
        "entities_expected",
        "entities_produced",
        "entities_matched",
        "edges_expected",
        "edges_produced",
        "edges_matched",
        "props_expected",
        "props_produced",
        "props_matched",
        "spans_checked",
        "spans_hit",
        "forbidden_written",
    )
    for ran_case, scored_case in zip(
        sorted(run_row["cases"], key=lambda c: c["name"]),
        sorted(score_row["cases"], key=lambda c: c["name"]),
        strict=True,
    ):
        assert scored_case["score"] == ran_case["score"] == 1.0
        for key in keys:
            assert scored_case["counts"][key] == ran_case["counts"][key], key
    # What saved casts cannot tell is not measured, not a failure.
    assert score_row["rates"]["conformance"] is None
    assert score_row["rates"]["tokens_per_episode"] is None
    assert "n/m" in scored.output


def test_an_episode_without_a_saved_cast_counts_as_missed(tmp_path: Path):
    name = "direction/helpdesk"
    dropped = BY_NAME[name].episodes[0].source_id
    cast = _saved_cast(name, tmp_path / "cast.json", drop=dropped)
    data = json.loads(cast.read_text())
    data["mentions"].append(
        {"source_id": "not-in-the-corpus", "schema_ref": "x@1", "records": []}
    )
    cast.write_text(json.dumps(data))
    out = tmp_path / "out"
    result = CliRunner().invoke(
        bench, ["score", str(cast), "--case", name, "--row", "saved", "--out", str(out)]
    )
    assert result.exit_code == 0, result.output
    (row,) = _report(out)["rows"]
    assert row["label"] == "saved"
    assert row["unmatched_casts"] == ["not-in-the-corpus"]
    (case,) = row["cases"]
    episode = next(e for e in case["episodes"] if e["source_id"] == dropped)
    assert episode["status"] == "failed" and episode["failure"] == "not_saved"
    counts = episode["counts"]
    assert counts["entities_expected"] > 0 and counts["entities_matched"] == 0
    assert episode["misses"]["expected_entities"]
    assert case["score"] is not None and case["score"] < 1.0
    assert dropped in result.output  # listed under Misses


async def test_an_allowed_item_is_neither_right_nor_wrong(make_client):
    case = BY_NAME["direction/plant"]
    source_id = "direction-plant-1"
    full = case.expected[source_id]
    client, _ = make_client(oracle_reply(full))
    caster = SchemaCaster(case.schema_, settings=SchemaCastSettings(), client=client)
    episode = next(e for e in case.episodes if e.source_id == source_id)
    outcome = await caster.cast_episode(episode)
    raw = full.model_dump(by_alias=True)
    p1 = raw["vertices"].pop("p1")
    feeds = raw.pop("edges")
    # Only P-2 is required; P-1 and the relation are neither required nor wrong.
    strict = ExpectedEpisode.model_validate({**raw, "edges": []})
    lenient = ExpectedEpisode.model_validate(
        {**raw, "edges": [], "allowed": {"vertices": {"p1": p1}, "edges": feeds}}
    )

    misses = Misses()
    counts = score_episode(outcome, strict, case.schema_, episode.text, misses=misses)
    assert (counts.entities_produced, counts.entities_matched) == (2, 1)
    assert (counts.edges_produced, counts.edges_matched) == (1, 0)
    assert misses.produced_entities == ['Machine "press P-1"']
    assert misses.produced_edges == ['FEEDS "Press P-2 is fed by press P-1"']

    misses = Misses()
    counts = score_episode(outcome, lenient, case.schema_, episode.text, misses=misses)
    assert (counts.entities_expected, counts.entities_produced) == (1, 1)
    assert (counts.edges_expected, counts.edges_produced) == (0, 0)
    assert case_score(counts) == 1.0
    assert misses.empty


def test_allowed_handles_must_differ_from_expected_ones():
    with pytest.raises(ValueError, match="allowed reuses"):
        ExpectedEpisode.model_validate(
            {
                "vertices": {"a": {"type": "T", "quote": "x"}},
                "allowed": {"vertices": {"a": {"type": "T", "quote": "x"}}},
            }
        )
