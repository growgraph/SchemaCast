"""Settings, read from the environment.

Three prefixes, none shared with other tools: ``SCHEMACAST_LLM_`` for the model,
``SCHEMACAST_CACHE_`` for the response cache and ``SCHEMACAST_`` for the rest. No
``.env`` file is read; export the variables or pass the settings objects.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ResponseFormat = Literal["auto", "json_schema", "json_object"]

#: Model used when ``SCHEMACAST_LLM_MODEL`` is unset: an OpenAI model for high
#: volume. Any other endpoint needs its own model name.
DEFAULT_MODEL = "gpt-5.6-luna"


class LLMSettings(BaseSettings):
    """The model endpoint: any OpenAI-compatible server (OpenAI, Ollama ``/v1``, vLLM)."""

    model_config = SettingsConfigDict(env_prefix="SCHEMACAST_LLM_", extra="ignore")

    model: str = Field(
        default=DEFAULT_MODEL,
        description=(
            "Model name sent to the endpoint. The default is an OpenAI model; "
            "set it for any other endpoint."
        ),
    )
    base_url: str | None = Field(
        default=None,
        description=(
            "Endpoint base URL, e.g. ``http://localhost:11434/v1`` for Ollama. "
            "Unset means OpenAI."
        ),
    )
    api_key: SecretStr | None = Field(
        default=None,
        description=(
            "API key. Required for any provider call, including to endpoints "
            "that ignore it (set any value for a local server). Without one "
            "only cached responses are served."
        ),
    )
    temperature: float | None = Field(
        default=None,
        ge=0.0,
        le=2.0,
        description="Sampling temperature. Unset sends none: the model's default.",
    )
    reasoning_effort: str | None = Field(
        default=None,
        description="Passed through to models that accept ``reasoning_effort``.",
    )
    max_output_tokens: int | None = Field(
        default=None,
        ge=1,
        description=(
            "Output ceiling per call: ``max_completion_tokens`` to OpenAI, "
            "``max_tokens`` to any other endpoint."
        ),
    )
    response_format: ResponseFormat = Field(
        default="auto",
        description=(
            "``json_schema`` sends the instance schema for strict decoding; "
            "``json_object`` asks for JSON and puts the schema in the prompt; "
            "``auto`` tries ``json_schema`` and falls back to ``json_object`` "
            "for the rest of the run when the endpoint rejects it."
        ),
    )
    request_timeout_seconds: float = Field(
        default=120.0, gt=0, description="Per-request timeout."
    )
    transport_retries: int = Field(
        default=2,
        ge=0,
        description="SDK retries on connection errors, 429 and 5xx.",
    )
    parse_retries: int = Field(
        default=2,
        ge=0,
        description=(
            "Re-asks after a response that is not a JSON object, with the "
            "parse error shown to the model."
        ),
    )
    max_inflight: int = Field(
        default=8, ge=1, description="Concurrent provider requests per event loop."
    )


class CacheSettings(BaseSettings):
    """The on-disk response cache."""

    model_config = SettingsConfigDict(env_prefix="SCHEMACAST_CACHE_", extra="ignore")

    enabled: bool = Field(default=True, description="Read and write cached responses.")
    dir: Path | None = Field(
        default=None,
        description=(
            "Cache directory. Unset: ``./.test_cache`` under pytest, else "
            "``$XDG_CACHE_HOME/schemacast`` or ``~/.cache/schemacast``."
        ),
    )
    read_only: bool = Field(
        default=False, description="Serve cached responses but write no new ones."
    )
    require_hit: bool = Field(
        default=False,
        description=(
            "A cache miss raises instead of calling the model: a replay that "
            "provably makes no request."
        ),
    )

    @model_validator(mode="after")
    def _require_hit_needs_cache(self) -> CacheSettings:
        if self.require_hit and not self.enabled:
            raise ValueError("require_hit needs the cache enabled")
        return self


class SchemaCastSettings(BaseSettings):
    """Top-level settings."""

    model_config = SettingsConfigDict(env_prefix="SCHEMACAST_", extra="ignore")

    llm: LLMSettings = Field(default_factory=LLMSettings)
    cache: CacheSettings = Field(default_factory=CacheSettings)

    max_episode_tokens: int = Field(
        default=4000,
        ge=1,
        description=(
            "Ceiling on an episode's estimated tokens (characters / 4). Episodes "
            "are never chunked: one over the ceiling is refused before any call."
        ),
    )
    slice_max_tokens: int | None = Field(
        default=4000, ge=1, description="Token budget of the schema slice."
    )
    slice_max_elements: int | None = Field(
        default=60, ge=1, description="Vertex and edge budget of the schema slice."
    )
    user_instruction: str | None = Field(
        default=None,
        description="Extra guidance placed after the rules, before the text.",
    )


class ServeSettings(BaseSettings):
    """``schemacast serve``: the HTTP service."""

    model_config = SettingsConfigDict(env_prefix="SCHEMACAST_SERVE_", extra="ignore")

    api_key: SecretStr | None = Field(
        default=None,
        description=(
            "Bearer token every request but ``/health`` must carry. Without one "
            "the service refuses to cast unless ``allow_anonymous`` is set."
        ),
    )
    allow_anonymous: bool = Field(
        default=False,
        description="Serve without a key; for local development only.",
    )
    max_episodes: int = Field(
        default=200, ge=1, description="Most episodes one request may carry."
    )
    host: str = Field(default="127.0.0.1", description="Address to bind.")
    port: int = Field(default=8997, ge=1, le=65535, description="Port to bind.")
