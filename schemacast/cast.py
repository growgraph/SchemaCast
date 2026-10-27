"""Cast episodes into a governed schema: the whole pipeline.

Per episode: select a slice → one extraction call → validate → at most two
typed repairs → quarantine what still fails → a record graph (graflo
``RecordGraph``, quotes and spans in ``evidence``). The batch's record graphs
are then resolved by graflo, without a model and without a
database: what their keys identify becomes ``output``, the rest is staged. A
write resolves them again against the database (``schemacast.write``).
No critic pass: a valid extraction is never judged a second time.

An episode that produces nothing is quarantined and the run goes on; so is one
whose request fails at the provider (``provider_error``), whose reply stops at
the output limit (``truncated``) or that the model refuses (``refused``). A
fault in the deployment — no key, a refused configuration, a cache miss while
replaying — stops the run, because every other episode would fail the same way.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterable
from importlib.metadata import version
from typing import Any, Literal

from graflo.architecture.schema.document import Schema
from graflo.architecture.schema.graflo_output import GraFloOutput
from graflo.link.records import RecordGraph, Resolution
from openai import APIError
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from schemacast import __version__
from schemacast.config import SchemaCastSettings
from schemacast.episode import Episode
from schemacast.extract import (
    INSTANCE_SCHEMA_NAME,
    EpisodeTooLongError,
    WireExtraction,
    WireShapeError,
    episode_shape,
    extract,
)
from schemacast.ground import Grounding, UnmappedText, groundings
from schemacast.llm.cache import LLMCache, cache_key_config
from schemacast.llm.client import (
    LLMClient,
    LLMRefusalError,
    LLMReplyError,
    LLMRequestTimeoutError,
    LLMTruncatedError,
)
from schemacast.llm.jsonparse import LLMJsonParseError
from schemacast.llm.usage import UsageTracker, track_usage
from schemacast.prompt import build_messages
from schemacast.repair import repair_messages
from schemacast.schema_io import schema_ref
from schemacast.select import (
    RetrievalMetrics,
    Selection,
    SelectionConfig,
    SelectionError,
    select,
)
from schemacast.sink import MemoryQuarantineSink, QuarantineRecord, QuarantineSink
from schemacast.validate import Finding, Fix, Validation, validate

#: Version of the files a cast writes; bumped when a record's meaning changes.
OUTPUT_FORMAT_VERSION = 6

#: Repair requests after the first reply, at most.
MAX_REPAIRS = 2

EpisodeStatus = Literal["ok", "partial", "failed"]

#: Faults of one request that say nothing about the deployment: the episode is
#: quarantined and the run goes on.
EPISODE_FAULTS = (LLMReplyError, LLMRequestTimeoutError, APIError)


def _fault_kind(error: BaseException) -> str:
    if isinstance(error, LLMTruncatedError):
        return "truncated"
    if isinstance(error, LLMRefusalError):
        return "refused"
    return "provider_error"


def _findings_key(findings: list[Finding]) -> list[tuple[str, str, str, str]]:
    return sorted(
        (
            f.kind,
            f.path,
            f.detail or "",
            json.dumps(f.fragment, sort_keys=True, ensure_ascii=False),
        )
        for f in findings
    )


class EpisodeResult(BaseModel):
    """What happened to one episode. Free of timing and cache facts, so a
    replay reproduces it byte for byte."""

    source_id: str
    status: EpisodeStatus = Field(
        description="``ok``: nothing left out; ``partial``: some items quarantined; "
        "``failed``: nothing written."
    )
    failure: str | None = Field(
        default=None, description="Why a failed episode failed."
    )
    selection: Literal["whole", "slice"] | None = None
    retrieval: RetrievalMetrics | None = None
    requests: int = Field(
        default=0, description="Model requests, re-asks and repairs included."
    )
    repairs: int = Field(default=0, description="Typed repair rounds.")
    repair_failure: str | None = Field(
        default=None,
        description="Why a repair request failed; the best earlier attempt is kept.",
    )
    mentions: int = Field(default=0, description="Records read from the text.")
    relations: int = 0
    known: int = Field(
        default=0, description="Known entities a relation uses (refs ``k1``…)."
    )
    quarantined: int = Field(default=0, description="Items left out.")
    unmapped: int = 0
    fixes: list[Fix] = Field(default_factory=list)


class RunManifest(BaseModel):
    """How a cast was produced."""

    schemacast_version: str
    graflo_version: str
    output_format_version: int = OUTPUT_FORMAT_VERSION
    schema_ref: str
    model: dict[str, Any] = Field(
        description="Every model setting that reaches the provider."
    )
    max_episode_tokens: int
    slice_max_tokens: int | None
    slice_max_elements: int | None
    user_instruction: bool
    episodes: int
    status: dict[str, int]
    mentions: int
    relations: int
    vertices: int = Field(description="Vertices the batch's keys identify.")
    edges: int = Field(description="Edges between identified vertices.")
    staged: int = Field(description="Mentions not identified without a database.")
    quarantined: int
    unmapped: int
    usage: UsageTracker


class CastResult(BaseModel):
    """The output of a cast."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    graphs: list[RecordGraph] = Field(
        default_factory=list, description="One record graph per episode cast."
    )
    resolution: Resolution = Field(
        description="The graphs resolved by their keys, without a database."
    )
    output: GraFloOutput = Field(
        description="The full schema and what the resolution identified."
    )
    groundings: list[Grounding] = Field(default_factory=list)
    unmapped: dict[str, list[UnmappedText]] = Field(
        default_factory=dict,
        description="By episode source id: what the text states that no type covers.",
    )
    episodes: list[EpisodeResult] = Field(default_factory=list)
    manifest: RunManifest


class EpisodeOutcome(BaseModel):
    """One episode's result with the intermediate steps that produced it."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    result: EpisodeResult
    selection: Selection | None = Field(
        default=None, description="The slice; None when selection failed."
    )
    first: Validation | None = Field(
        default=None, description="The first reply, checked; None when there was none."
    )
    validation: Validation | None = Field(
        default=None, description="The attempt kept, after repairs."
    )
    graph: RecordGraph | None = None
    groundings: list[Grounding] = Field(default_factory=list)
    quarantine: list[QuarantineRecord] = Field(
        default_factory=list, description="What the episode left out."
    )


def _extractor() -> str:
    return f"schemacast/{__version__}"


class SchemaCaster:
    """Casts episodes into one governed schema.

    Args:
        schema: The governed schema; every write conforms to it.
        settings: Defaults to the environment.
        client: Defaults to one built from *settings*.
        sink: Where quarantined records go. Defaults to memory
            (:attr:`sink` then holds them).
        selection: Selection tuning; its budget defaults to the settings'.
        max_concurrent_episodes: Episodes in flight at once.
        owned: Connector-owned vertex types: linked by key, never created.
    """

    def __init__(
        self,
        schema: Schema,
        *,
        settings: SchemaCastSettings | None = None,
        client: LLMClient | None = None,
        sink: QuarantineSink | None = None,
        selection: SelectionConfig | None = None,
        max_concurrent_episodes: int = 8,
        owned: frozenset[str] = frozenset(),
    ) -> None:
        self.schema = schema
        self.owned = owned
        self.settings = settings or SchemaCastSettings()
        self.client = client or LLMClient(
            self.settings.llm, LLMCache(self.settings.cache)
        )
        self.sink: QuarantineSink = sink if sink is not None else MemoryQuarantineSink()
        self.selection = selection or SelectionConfig(
            max_tokens=self.settings.slice_max_tokens,
            max_elements=self.settings.slice_max_elements,
        )
        self.max_concurrent_episodes = max(1, max_concurrent_episodes)

    def _fail(
        self,
        episode: Episode,
        failure: str,
        message: str,
        *,
        raw: str | None = None,
        sliced: Selection | None = None,
        **known: Any,
    ) -> EpisodeOutcome:
        record = QuarantineRecord(
            record="episode",
            source_id=episode.source_id,
            kind=failure,
            message=message,
            raw_output=raw,
            attempts=known.get("requests", 0),
            schema_ref=schema_ref(self.schema),
        )
        return EpisodeOutcome(
            result=EpisodeResult(
                source_id=episode.source_id, status="failed", failure=failure, **known
            ),
            selection=sliced,
            quarantine=[record],
        )

    async def cast_episode(self, episode: Episode) -> EpisodeOutcome:
        """Cast one episode and write what it left out to the sink."""
        outcome = await self._cast_episode(episode)
        await self.sink.write_records(outcome.quarantine)
        return outcome

    async def _cast_episode(self, episode: Episode) -> EpisodeOutcome:
        try:
            selection = select(
                self.schema,
                episode.text,
                self.selection,
                pinned=[entity.vertex_type for entity in episode.known],
            )
        except SelectionError as error:
            return self._fail(
                episode,
                f"selection_{error.reason}",
                str(error),
                retrieval=error.metrics,
            )
        slice_ = selection.slice
        known: dict[str, Any] = {
            "selection": selection.mode,
            "retrieval": selection.metrics,
        }
        try:
            extraction = await extract(
                self.client,
                slice_,
                episode,
                user_instruction=self.settings.user_instruction,
                max_episode_tokens=self.settings.max_episode_tokens,
            )
        except EpisodeTooLongError as error:
            return self._fail(
                episode, "too_long", str(error), sliced=selection, **known
            )
        except WireShapeError as error:
            return self._fail(
                episode,
                "wire_shape",
                str(error),
                raw=error.content,
                sliced=selection,
                **known,
            )
        except LLMJsonParseError as error:
            return self._fail(
                episode,
                "unparseable",
                str(error),
                raw=error.doc,
                sliced=selection,
                **known,
            )
        except EPISODE_FAULTS as error:
            return self._fail(
                episode,
                _fault_kind(error),
                str(error),
                raw=error.content if isinstance(error, LLMReplyError) else None,
                sliced=selection,
                requests=1,
                **known,
            )

        requests = extraction.completion.attempts
        messages = build_messages(
            slice_, episode, user_instruction=self.settings.user_instruction
        )
        shape = episode_shape(slice_)
        current = first = validate(
            extraction.wire, slice_, episode.text, episode.context, episode.known
        )
        current_reply = extraction.completion.content
        best, best_reply = current, current_reply
        repairs = 0
        repair_failure: str | None = None
        while current.repairable and repairs < MAX_REPAIRS:
            repairs += 1
            asked = _findings_key(current.repairable)
            try:
                completion = await self.client.acomplete_json(
                    repair_messages(messages, current_reply, current.repairable),
                    shape,
                    schema_name=INSTANCE_SCHEMA_NAME,
                    stage="repair",
                    source_id=episode.source_id,
                )
                requests += completion.attempts
                wire = WireExtraction.model_validate(completion.data)
            except LLMJsonParseError:
                requests += 1
                repair_failure = "unparseable"
                break
            except ValidationError:
                repair_failure = "wire_shape"
                break
            except EPISODE_FAULTS as error:
                requests += 1
                repair_failure = _fault_kind(error)
                break
            current = validate(
                wire, slice_, episode.text, episode.context, episode.known
            )
            current_reply = completion.content
            if current.kept >= best.kept:
                best, best_reply = current, current_reply
            # The same findings again: the slice does not let the model fix them.
            if _findings_key(current.repairable) == asked:
                break

        known.update(requests=requests, repairs=repairs, repair_failure=repair_failure)
        quarantine = self._records(episode, best, best_reply, requests)
        # The slice's ref, which keys minted for keyless entities depend on.
        graph = best.graph(
            episode.source_id, schema_ref(slice_), episode.reference_time
        )
        removed = best.removed
        status: EpisodeStatus = (
            "ok" if not removed else "partial" if best.kept else "failed"
        )
        return EpisodeOutcome(
            result=EpisodeResult(
                source_id=episode.source_id,
                status=status,
                failure="nothing_valid" if status == "failed" else None,
                mentions=len(best.mentions),
                relations=len(graph.relations),
                known=len(best.known),
                quarantined=len(removed),
                unmapped=len(best.unmapped),
                fixes=best.fixes,
                **known,
            ),
            selection=selection,
            first=first,
            validation=best,
            graph=graph,
            groundings=groundings(
                graph, episode, extractor=_extractor(), model=self.client.settings.model
            ),
            quarantine=quarantine,
        )

    def _records(
        self, episode: Episode, validation: Validation, reply: str, requests: int
    ) -> list[QuarantineRecord]:
        ref = schema_ref(self.schema)
        records = [
            QuarantineRecord(
                record="finding",
                source_id=episode.source_id,
                kind=finding.kind,
                detail=finding.detail,
                path=finding.path,
                message=finding.message,
                quote=finding.fragment.get("quote"),
                fragment=finding.fragment,
                attempts=requests,
                raw_output=reply,
                schema_ref=ref,
            )
            for finding in validation.removed
        ]
        records += [
            QuarantineRecord(
                record="unmapped",
                source_id=episode.source_id,
                kind="unmapped",
                message=item.note,
                quote=item.quote,
                char_start=item.span.start,
                char_end=item.span.end,
                schema_ref=ref,
            )
            for item in validation.unmapped
        ]
        return records

    async def cast(self, episodes: Iterable[Episode]) -> CastResult:
        """Cast *episodes*; results and quarantine records keep their order.

        Raises:
            LLMConfigurationError: the deployment is misconfigured.
            CacheMissError: a miss while the cache requires hits.
        """
        items = list(episodes)
        gate = asyncio.Semaphore(self.max_concurrent_episodes)

        async def _one(episode: Episode) -> EpisodeOutcome:
            async with gate:
                return await self._cast_episode(episode)

        with track_usage() as usage:
            try:
                async with asyncio.TaskGroup() as group:
                    tasks = [group.create_task(_one(episode)) for episode in items]
            except ExceptionGroup as errors:
                raise errors.exceptions[0] from errors
        outcomes = [task.result() for task in tasks]
        for outcome in outcomes:
            await self.sink.write_records(outcome.quarantine)
        return self._assemble(outcomes, usage)

    def _assemble(
        self, outcomes: list[EpisodeOutcome], usage: UsageTracker
    ) -> CastResult:
        from graflo.link.resolve import promote, resolve_records

        graphs = [o.graph for o in outcomes if o.graph is not None]
        resolution = resolve_records(self.schema, graphs, owned=self.owned)
        container = promote(self.schema, resolution)
        results = [o.result for o in outcomes]
        status = {name: 0 for name in ("ok", "partial", "failed")}
        for result in results:
            status[result.status] += 1
        manifest = RunManifest(
            schemacast_version=__version__,
            graflo_version=version("graflo"),
            schema_ref=schema_ref(self.schema),
            model={
                key: value
                for key, value in cache_key_config(self.client.settings).items()
                if key != "cache_format_version"
            },
            max_episode_tokens=self.settings.max_episode_tokens,
            slice_max_tokens=self.selection.max_tokens,
            slice_max_elements=self.selection.max_elements,
            user_instruction=bool(self.settings.user_instruction),
            episodes=len(results),
            status=status,
            mentions=sum(r.mentions for r in results),
            relations=sum(r.relations for r in results),
            vertices=sum(len(docs) for docs in container.vertices.values()),
            edges=sum(len(triples) for triples in container.edges.values()),
            staged=len(resolution.staged.records),
            quarantined=sum(r.quarantined for r in results),
            unmapped=sum(r.unmapped for r in results),
            usage=usage,
        )
        return CastResult(
            graphs=graphs,
            resolution=resolution,
            output=GraFloOutput.model_validate(
                {"schema": self.schema, "data": container}
            ),
            groundings=[g for o in outcomes for g in o.groundings],
            unmapped={
                o.result.source_id: o.validation.unmapped
                for o in outcomes
                if o.validation is not None and o.validation.unmapped
            },
            episodes=results,
            manifest=manifest,
        )
