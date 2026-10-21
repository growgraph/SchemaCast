"""Run the corpus through a model, or the oracle, and score every episode."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Callable
from importlib.metadata import version
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from schemacast import __version__
from schemacast.bench.corpus import (
    CORPUS_DIGEST,
    CORPUS_VERSION,
    BenchCase,
    ExpectedEpisode,
    ExpectedPicks,
    corpus_digest,
)
from schemacast.bench.oracle import ORACLE, oracle_client, oracle_pick_reply
from schemacast.bench.score import (
    Counts,
    ref_handles,
    score_episode,
    score_picks,
    score_recast,
    total,
)
from schemacast.cast import EpisodeOutcome, SchemaCaster
from schemacast.config import SchemaCastSettings
from schemacast.episode import Episode
from schemacast.llm.cache import LLMCache, cache_key_config, default_cache_dir
from schemacast.llm.client import LLMClient
from schemacast.llm.usage import UsageTracker, track_usage
from schemacast.pick import PickItem, pick_episode

#: Environment variable naming a JSON price table:
#: ``{"<model>": {"input_per_1m": 0.15, "output_per_1m": 0.6}}``; keys starting
#: with ``_`` are comments. No prices ship with the package.
PRICES_ENV = "SCHEMACAST_BENCH_PRICES"

#: The second cast of a re-cast case is cached here, beside the first, so a
#: replay of the benchmark repeats both without a request.
RECAST_CACHE_SUBDIR = "recast"


class EpisodeRecord(BaseModel):
    """One episode's outcome: enough to pair two runs episode by episode."""

    source_id: str
    status: str
    failure: str | None
    counts: Counts


class CaseRecord(BaseModel):
    """One case's episodes and their sum."""

    name: str
    family: str
    instance: str
    split: str
    digest: str | None = Field(
        default=None,
        description="The case's content digest; absent before corpus 1.1.0.",
    )
    counts: Counts
    episodes: list[EpisodeRecord]


class Row(BaseModel):
    """Every selected case cast by one model."""

    label: str
    model: dict[str, Any] = Field(description="Model settings that reach the provider.")
    usd: float | None = Field(
        default=None,
        description="List price of the tokens used, billed or replayed; None "
        "without a price for this model.",
    )
    cases: list[CaseRecord]

    @property
    def counts(self) -> Counts:
        return total([case.counts for case in self.cases])


class BenchReport(BaseModel):
    """A benchmark run."""

    corpus_version: str = CORPUS_VERSION
    corpus_digest: str
    corpus_modified: bool = Field(
        description="The cases differ from the pinned digest: not publishable."
    )
    split: str
    schemacast_version: str = __version__
    graflo_version: str = Field(default_factory=lambda: version("graflo"))
    started: str
    prices_source: str | None = None
    rows: list[Row]

    @property
    def publishable(self) -> bool:
        return self.split == "test" and not self.corpus_modified


def load_prices(
    path: str | None = None,
) -> tuple[dict[str, dict[str, float]], str | None]:
    """The price table and its ``_source`` note; empty when none is configured."""
    source = path or os.environ.get(PRICES_ENV)
    if not source:
        return {}, None
    raw = json.loads(Path(source).read_text(encoding="utf-8"))
    note = raw.get("_source")
    return {k: v for k, v in raw.items() if not k.startswith("_")}, note


def price(counts: Counts, entry: dict[str, float] | None) -> float | None:
    """USD for *counts*' tokens at *entry*'s rates."""
    if not entry:
        return None
    return (
        counts.input_tokens * float(entry.get("input_per_1m", 0.0))
        + counts.output_tokens * float(entry.get("output_per_1m", 0.0))
    ) / 1_000_000


def case_settings(case: BenchCase, base: SchemaCastSettings) -> SchemaCastSettings:
    """*base* with the settings *case* pins."""
    pinned = case.settings.model_dump(include=case.settings.model_fields_set)
    return base.model_copy(update=pinned)


def recast_settings(base: SchemaCastSettings) -> SchemaCastSettings:
    """*base* with its own cache directory, so the second cast makes a fresh call."""
    root = base.cache.dir or default_cache_dir()
    cache = base.cache.model_copy(update={"dir": root / RECAST_CACHE_SUBDIR})
    return base.model_copy(update={"cache": cache})


#: Oracle replies to pick calls, by episode source id, written before each call.
PickScripts = dict[str, str]

ClientFactory = Callable[[BenchCase, SchemaCastSettings, PickScripts], LLMClient]


def model_clients(settings: SchemaCastSettings) -> ClientFactory:
    """Clients for the configured model, one cache per settings."""

    def build(
        _case: BenchCase, case_settings_: SchemaCastSettings, _picks: PickScripts
    ) -> LLMClient:
        return LLMClient(case_settings_.llm, LLMCache(case_settings_.cache))

    return build


def oracle_clients() -> ClientFactory:
    """Oracle clients, scripted per case."""

    def build(
        case: BenchCase, _settings: SchemaCastSettings, picks: PickScripts
    ) -> LLMClient:
        return oracle_client(case.expected, picks)

    return build


def _add_tokens(counts: Counts, usage: UsageTracker) -> None:
    counts.input_tokens += (usage.billed.input_tokens or 0) + (
        usage.replayed.input_tokens or 0
    )
    counts.output_tokens += (usage.billed.output_tokens or 0) + (
        usage.replayed.output_tokens or 0
    )
    counts.billed_input_tokens += usage.billed.input_tokens or 0
    counts.billed_output_tokens += usage.billed.output_tokens or 0


def pick_item(
    outcome: EpisodeOutcome,
    episode: Episode,
    spec: ExpectedPicks,
    refs: dict[str, str],
    schema: Any,
) -> tuple[PickItem, dict[str, dict[str, Any] | None]]:
    """The pick input for a cast episode's staged mentions the case gives
    candidates for, and the expected key per mention ref."""
    from graflo.link.records import EntityCandidate, StagedCandidates
    from graflo.link.resolve import resolve_records

    graph = outcome.graph
    resolution = resolve_records(schema, [graph] if graph is not None else [])
    by_handle = {handle: ref for ref, handle in refs.items()}

    def entity(item: Any) -> EntityCandidate:
        return EntityCandidate(
            vertex_type=item.type,
            key=item.key,
            label=item.label,
            lane=item.lane,
            matched=item.matched,
            relation=item.relation,
            anchor=by_handle.get(item.anchor, item.anchor) if item.anchor else None,
            role=item.role,
        )

    staged = []
    wanted: dict[str, dict[str, Any] | None] = {}
    for entry in resolution.staged.records:
        handle = refs.get(entry.record.ref)
        if handle not in spec.mentions:
            continue
        expected = spec.mentions[handle]
        staged.append(
            StagedCandidates(
                source_id=entry.source_id,
                record=entry.record,
                reason=entry.reason,
                candidates=[entity(c) for c in expected.candidates],
            )
        )
        wanted[entry.record.ref] = expected.pick
    item = PickItem(
        episode=episode,
        staged=staged,
        entities=[entity(e) for e in spec.entities],
        graph=graph,
    )
    return item, wanted


async def _episode(
    caster: SchemaCaster,
    recaster: SchemaCaster | None,
    episode: Episode,
    expected: ExpectedEpisode,
    case: BenchCase,
    gate: asyncio.Semaphore,
    picks: PickScripts,
) -> EpisodeRecord:
    async with gate:
        with track_usage() as usage:
            outcome = await caster.cast_episode(episode)
        context = {item.source_id: item.text for item in episode.context}
        counts = score_episode(
            outcome, expected, case.schema_, episode.text, usage, context
        )
        if recaster is not None:
            with track_usage() as again:
                second = await recaster.cast_episode(episode)
            counts = counts + score_recast(outcome, second, case.schema_)
            _add_tokens(counts, again)
        spec = case.picks.get(episode.source_id)
        if spec is not None:
            refs = ref_handles(outcome, expected, case.schema_, episode.text, context)
            item, wanted = pick_item(outcome, episode, spec, refs, case.schema_)
            picks[episode.source_id] = json.dumps(oracle_pick_reply(item, wanted))
            with track_usage() as picked:
                found, result = await pick_episode(
                    caster.client,
                    item,
                    user_instruction=caster.settings.user_instruction,
                )
            counts = counts + score_picks(found, refs, spec)
            counts.requests += result.requests if result.status != "skipped" else 0
            _add_tokens(counts, picked)
    return EpisodeRecord(
        source_id=episode.source_id,
        status=outcome.result.status,
        failure=outcome.result.failure,
        counts=counts,
    )


async def run_case(
    case: BenchCase,
    settings: SchemaCastSettings,
    clients: ClientFactory,
    *,
    concurrency: int = 8,
) -> CaseRecord:
    """Cast and score every episode of *case*.

    Raises:
        LLMConfigurationError: the deployment is misconfigured.
        CacheMissError: a miss while the cache requires hits.
    """
    pinned = case_settings(case, settings)
    picks: PickScripts = {}
    caster = SchemaCaster(
        case.schema_, settings=pinned, client=clients(case, pinned, picks)
    )
    recaster = None
    if case.recast:
        again = recast_settings(pinned)
        recaster = SchemaCaster(
            case.schema_, settings=again, client=clients(case, again, picks)
        )
    gate = asyncio.Semaphore(max(1, concurrency))
    try:
        async with asyncio.TaskGroup() as group:
            tasks = [
                group.create_task(
                    _episode(
                        caster,
                        recaster,
                        episode,
                        case.expected[episode.source_id],
                        case,
                        gate,
                        picks,
                    )
                )
                for episode in case.episodes
            ]
    except ExceptionGroup as errors:
        raise errors.exceptions[0] from errors
    episodes = [task.result() for task in tasks]
    return CaseRecord(
        name=case.name,
        family=case.family,
        instance=case.instance,
        split=case.split,
        digest=case.digest,
        counts=total([e.counts for e in episodes]),
        episodes=episodes,
    )


async def run_row(
    cases: list[BenchCase],
    settings: SchemaCastSettings,
    *,
    oracle: bool = False,
    concurrency: int = 8,
    prices: dict[str, dict[str, float]] | None = None,
) -> Row:
    """One row: every case cast by the configured model, or by the oracle."""
    clients = oracle_clients() if oracle else model_clients(settings)
    records = [
        await run_case(case, settings, clients, concurrency=concurrency)
        for case in cases
    ]
    label = ORACLE if oracle else settings.llm.model
    model = (
        {"model": ORACLE}
        if oracle
        else {
            key: value
            for key, value in cache_key_config(settings.llm).items()
            if key != "cache_format_version"
        }
    )
    row = Row(label=label, model=model, cases=records)
    if not oracle:
        row.usd = price(row.counts, (prices or {}).get(label))
    return row


def new_report(
    split: str, started: str, rows: list[Row], prices_source: str | None
) -> BenchReport:
    """A report over *rows*, stamped with the corpus it ran against."""
    digest = corpus_digest()
    return BenchReport(
        corpus_digest=digest,
        corpus_modified=digest != CORPUS_DIGEST,
        split=split,
        started=started,
        prices_source=prices_source,
        rows=rows,
    )
