"""Score saved cast output against a corpus, without a model.

A saved cast is a JSON file holding a ``POST /cast`` response body, or a
wrapper ``{"episodes": [...], "cast": <body>}``: the episodes sent and the body
returned. Each episode's record graph is scored as the run scores a fresh
cast. What only a live cast knows — the first reply before repair, the slice,
tokens, a re-cast, picks — is not measured and is reported as such.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from graflo.link.records import RecordGraph

from schemacast.bench.corpus import BenchCase
from schemacast.bench.run import CaseRecord, EpisodeRecord, Row
from schemacast.bench.score import Counts, Misses, score_episode, total
from schemacast.cast import EpisodeOutcome, EpisodeResult
from schemacast.validate import Validation

#: Counts a saved cast cannot supply; they stay zero and read as not measured.
UNMEASURED_COUNTS = frozenset(
    {
        "emitted_first",
        "nonconforming_first",
        "emitted_final",
        "nonconforming_final",
        "ungrounded_first",
        "vertices_first",
        "unknown_type_first",
        "edges_first",
        "undeclared_edge_first",
        "properties_first",
        "undeclared_property_first",
        "unfaithful_value_first",
        "sliced",
        "gold_types",
        "gold_types_seeded",
        "gold_types_in_slice",
        "gold_edges",
        "gold_edges_in_slice",
        "recast_vertices",
        "recast_added",
        "recast_minted",
        "recast_duplicated",
        "picks_expected",
        "picks_offered",
        "picks_correct",
        "picks_linked",
        "picks_wrong",
        "picks_rejected",
        "input_tokens",
        "output_tokens",
        "billed_input_tokens",
        "billed_output_tokens",
    }
)

#: Status of a corpus episode no saved cast holds.
NOT_SAVED = "not_saved"


class SavedCastError(ValueError):
    """A file that is not a saved cast, or two casts of one episode."""


class SavedCasts:
    """Episode outcomes read from saved casts, by source id."""

    def __init__(self) -> None:
        self.outcomes: dict[str, EpisodeOutcome] = {}
        self.models: list[dict[str, Any]] = []

    def add(self, path: Path) -> None:
        """Read one saved cast.

        Raises:
            SavedCastError: not a cast body or wrapper, or an episode cast twice.
        """
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise SavedCastError(f"{path}: not JSON: {error.msg}") from error
        body = data.get("cast") if isinstance(data, dict) and "cast" in data else data
        if not isinstance(body, dict) or not {"mentions", "episodes"} <= set(body):
            raise SavedCastError(f"{path}: not a cast body (mentions, episodes)")
        try:
            graphs = {
                graph.source_id: graph
                for graph in (RecordGraph.model_validate(g) for g in body["mentions"])
            }
            results = {
                result.source_id: result
                for result in (
                    EpisodeResult.model_validate(r) for r in body["episodes"]
                )
            }
        except ValueError as error:
            raise SavedCastError(f"{path}: {error}") from error
        model = (body.get("manifest") or {}).get("model")
        if isinstance(model, dict):
            self.models.append(model)
        for source_id in [*results, *(g for g in graphs if g not in results)]:
            if source_id in self.outcomes:
                raise SavedCastError(f"{path}: episode {source_id!r} is cast twice")
            self.outcomes[source_id] = outcome(
                results.get(source_id)
                or EpisodeResult(source_id=source_id, status="ok"),
                graphs.get(source_id),
            )

    def label(self, paths: list[Path]) -> str:
        """The model the casts name, when they agree; else the first file's stem."""
        names = {str(model.get("model")) for model in self.models}
        if len(names) == 1 and len(self.models) == len(paths):
            return names.pop()
        return paths[0].stem


def outcome(result: EpisodeResult, graph: RecordGraph | None) -> EpisodeOutcome:
    """An episode outcome as a fresh cast would hold it, from its saved parts."""
    if graph is None:
        return EpisodeOutcome(result=result)
    known = [r for r in graph.records if "known" in (r.evidence or {})]
    validation = Validation(
        mentions=[r for r in graph.records if r not in known],
        relations=list(graph.relations),
        known=known,
        fixes=list(result.fixes),
    )
    return EpisodeOutcome(result=result, validation=validation, graph=graph)


def unmeasured(counts: Counts) -> Counts:
    """*counts* with what a saved cast cannot supply set to zero."""
    return counts.model_copy(update=dict.fromkeys(UNMEASURED_COUNTS, 0))


def score_saved(
    cases: list[BenchCase],
    saved: SavedCasts,
    label: str,
    *,
    corpus_ids: set[str] | None = None,
) -> Row:
    """One row: every episode of *cases* scored from *saved*.

    An episode with no saved cast counts as failed and fully missed. A saved
    cast of an episode not in *corpus_ids* (default: those of *cases*) is
    listed in ``unmatched_casts``.
    """
    records: list[CaseRecord] = []
    for case in cases:
        episodes: list[EpisodeRecord] = []
        for episode in case.episodes:
            found = saved.outcomes.get(episode.source_id) or EpisodeOutcome(
                result=EpisodeResult(
                    source_id=episode.source_id, status="failed", failure=NOT_SAVED
                )
            )
            context = {item.source_id: item.text for item in episode.context}
            misses = Misses()
            counts = score_episode(
                found,
                case.expected[episode.source_id],
                case.schema_,
                episode.text,
                None,
                context,
                misses,
                episode.known,
            )
            episodes.append(
                EpisodeRecord(
                    source_id=episode.source_id,
                    status=found.result.status,
                    failure=found.result.failure,
                    counts=unmeasured(counts),
                    misses=misses,
                )
            )
        records.append(
            CaseRecord(
                name=case.name,
                family=case.family,
                instance=case.instance,
                split=case.split,
                digest=case.digest,
                counts=total([e.counts for e in episodes]),
                episodes=episodes,
            )
        )
    known = (
        corpus_ids
        if corpus_ids is not None
        else {e.source_id for case in cases for e in case.episodes}
    )
    return Row(
        label=label,
        model=saved.models[0] if saved.models else {},
        cases=records,
        saved=True,
        unmatched_casts=sorted(set(saved.outcomes) - known),
    )
