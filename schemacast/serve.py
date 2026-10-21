"""``schemacast serve``: casting over HTTP, for a platform that holds the schema.

Two stateless, synchronous routes. ``POST /cast`` takes a schema inline, the
episodes and options, and returns what ``schemacast cast --out`` writes — the
record graphs (``mentions``), what their keys identify, what is staged,
groundings, unmapped text, quarantine, episode results and the run manifest.
The caller writes: it resolves the record graphs against its database (graflo
``write_record_graphs``). ``POST /pick`` takes episodes with their staged
records and candidates (graflo ``gather_candidates``) and returns one pick per
staged record; the caller closes what was picked. The service never writes to
a database and never calls back to the caller, so it holds no database
credentials and needs no URL allow-list.

The key is fail-closed: every route but ``/health`` needs
``Authorization: Bearer <SCHEMACAST_SERVE_API_KEY>``, and without a configured
key the service refuses to cast unless ``SCHEMACAST_SERVE_ALLOW_ANONYMOUS`` is
set. Model settings come from the service's own ``SCHEMACAST_LLM_*`` environment;
a request cannot pick the model or the endpoint.

Needs the ``server`` extra (FastAPI and uvicorn).
"""

from __future__ import annotations

import hmac
from importlib.metadata import version
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException
from graflo.link.records import RecordGraph, Staged
from pydantic import BaseModel, ConfigDict, Field

from schemacast import __version__
from schemacast.cast import EpisodeResult, RunManifest, SchemaCaster
from schemacast.config import SchemaCastSettings, ServeSettings
from schemacast.episode import Episode
from schemacast.ground import Grounding, UnmappedText
from schemacast.llm.cache import CacheMissError, LLMCache
from schemacast.llm.client import LLMClient, LLMConfigurationError
from schemacast.pick import Picker, PickItem, PickResult
from schemacast.schema_io import schema_from_mapping
from schemacast.sink import MemoryQuarantineSink, QuarantineRecord


class CastOptions(BaseModel):
    """Per-request settings; each one left out, or null, takes the service's value."""

    model_config = ConfigDict(extra="forbid")

    user_instruction: str | None = None
    slice_max_tokens: int | None = Field(default=None, ge=1)
    slice_max_elements: int | None = Field(default=None, ge=1)
    max_episode_tokens: int | None = Field(default=None, ge=1)


class CastRequest(BaseModel):
    """A schema (or a manifest carrying one) and the episodes to cast against it."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_: dict[str, Any] = Field(alias="schema")
    episodes: list[Episode] = Field(min_length=1)
    options: CastOptions = Field(default_factory=CastOptions)


class CastResponse(BaseModel):
    """What ``schemacast cast --out`` writes, in one body."""

    mentions: list[RecordGraph] = Field(
        description=(
            "One graflo ``RecordGraph`` per episode cast: what to resolve and "
            "write. Each record, relation and property carries its quote and "
            "span in ``evidence``."
        )
    )
    graph: dict[str, Any] = Field(
        description="graflo ``GraFloOutput``: what the keys identify without a database."
    )
    staged: Staged = Field(
        description="Records not identified without a database (``StagedRecord``)."
    )
    groundings: list[Grounding]
    unmapped: dict[str, list[UnmappedText]] = Field(
        default_factory=dict,
        description="By episode source id: what the text states that no type covers.",
    )
    quarantine: list[QuarantineRecord]
    episodes: list[EpisodeResult]
    manifest: RunManifest


class PickOptions(BaseModel):
    """Per-request pick settings; each one left out takes the service's value."""

    model_config = ConfigDict(extra="forbid")

    user_instruction: str | None = None


class PickRequest(BaseModel):
    """Episodes, each with its staged records, their candidates and the
    entities its metadata names."""

    model_config = ConfigDict(extra="forbid")

    episodes: list[PickItem] = Field(min_length=1)
    options: PickOptions = Field(default_factory=PickOptions)


class Health(BaseModel):
    """``GET /health``."""

    status: str = "ok"
    schemacast_version: str
    graflo_version: str
    auth_configured: bool
    max_episodes: int


def create_app(
    settings: SchemaCastSettings | None = None,
    serve: ServeSettings | None = None,
    *,
    client: LLMClient | None = None,
) -> FastAPI:
    """The service.

    Args:
        settings: Cast settings; defaults to the environment.
        serve: Service settings; defaults to the environment.
        client: The model client shared by every request; defaults to one
            built from *settings*.
    """
    settings = settings or SchemaCastSettings()
    serve = serve or ServeSettings()
    shared = client or LLMClient(settings.llm, LLMCache(settings.cache))
    app = FastAPI(title="SchemaCast", version=__version__)

    def require_key(
        authorization: Annotated[str | None, Header()] = None,
    ) -> None:
        expected = serve.api_key.get_secret_value() if serve.api_key else ""
        if not expected:
            if serve.allow_anonymous:
                return
            raise HTTPException(
                503,
                "SCHEMACAST_SERVE_API_KEY is not configured. Set it, or set "
                "SCHEMACAST_SERVE_ALLOW_ANONYMOUS=true for local development.",
            )
        given = authorization or ""
        if not hmac.compare_digest(given.encode(), f"Bearer {expected}".encode()):
            raise HTTPException(401, "Invalid or missing bearer token")

    @app.get("/health")
    def health() -> Health:
        return Health(
            schemacast_version=__version__,
            graflo_version=version("graflo"),
            auth_configured=serve.api_key is not None,
            max_episodes=serve.max_episodes,
        )

    @app.post("/cast", dependencies=[Depends(require_key)])
    async def cast(request: CastRequest) -> CastResponse:
        if len(request.episodes) > serve.max_episodes:
            raise HTTPException(
                413,
                f"{len(request.episodes)} episodes; at most {serve.max_episodes} "
                "per request.",
            )
        try:
            schema = schema_from_mapping(request.schema_)
        except (TypeError, ValueError) as error:
            raise HTTPException(422, f"Not a schema: {error}") from error
        overrides = request.options.model_dump(exclude_none=True)
        run_settings = settings.model_copy(update=overrides)
        sink = MemoryQuarantineSink()
        caster = SchemaCaster(schema, settings=run_settings, client=shared, sink=sink)
        try:
            result = await caster.cast(request.episodes)
        except LLMConfigurationError as error:
            raise HTTPException(503, f"The model is misconfigured: {error}") from error
        except CacheMissError as error:
            raise HTTPException(409, str(error)) from error
        return CastResponse(
            mentions=result.graphs,
            graph=result.output.to_dict(),
            staged=result.resolution.staged,
            groundings=result.groundings,
            unmapped=result.unmapped,
            quarantine=sink.records,
            episodes=result.episodes,
            manifest=result.manifest,
        )

    @app.post("/pick", dependencies=[Depends(require_key)])
    async def pick(request: PickRequest) -> PickResult:
        if len(request.episodes) > serve.max_episodes:
            raise HTTPException(
                413,
                f"{len(request.episodes)} episodes; at most {serve.max_episodes} "
                "per request.",
            )
        overrides = request.options.model_dump(exclude_none=True)
        picker = Picker(settings=settings.model_copy(update=overrides), client=shared)
        try:
            return await picker.pick(request.episodes)
        except LLMConfigurationError as error:
            raise HTTPException(503, f"The model is misconfigured: {error}") from error
        except CacheMissError as error:
            raise HTTPException(409, str(error)) from error

    return app


def run(serve: ServeSettings | None = None) -> None:
    """Serve on ``SCHEMACAST_SERVE_HOST``:``SCHEMACAST_SERVE_PORT``."""
    import uvicorn

    serve = serve or ServeSettings()
    uvicorn.run(create_app(serve=serve), host=serve.host, port=serve.port)
