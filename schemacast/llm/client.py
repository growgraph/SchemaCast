"""The model client: one JSON object per request, from any OpenAI-compatible endpoint.

:meth:`LLMClient.acomplete_json` is the only entry point. It serves a cached
response when there is one, and otherwise calls the endpoint — asking for
strict ``json_schema`` output first and falling back to ``json_object`` when
the endpoint rejects it — then parses the reply tolerantly, re-asking with the
parse error shown when the reply is not a JSON object.

Without an API key no request is made: only cached responses answer.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import weakref
from collections.abc import Callable, Sequence
from functools import partial
from typing import TYPE_CHECKING, Any, Literal, TypedDict

import openai
from pydantic import BaseModel, Field

from schemacast.config import LLMSettings, SchemaCastSettings
from schemacast.llm.cache import (
    CachedResponse,
    LLMCache,
    Stage,
    StoredRequest,
    cache_request,
    request_key,
)
from schemacast.llm.jsonparse import (
    LLMJsonParseError,
    feedback_excerpt,
    parse_json_object,
)
from schemacast.llm.usage import (
    TokenUsage,
    UsageTracker,
    active_tracker,
    token_usage_from_openai,
)

if TYPE_CHECKING:
    import httpx

logger = logging.getLogger(__name__)

Mode = Literal["json_schema", "json_object"]

#: Base delay for re-ask backoff, doubled per attempt, capped, with jitter so
#: concurrent re-asks do not land in lockstep.
RETRY_BACKOFF_BASE_SECONDS = 0.5
RETRY_BACKOFF_MAX_SECONDS = 8.0

#: Appended as the last message in ``json_object`` mode, where the endpoint
#: does not receive the schema itself.
#: Sent when ``base_url`` is unset, so the SDK never falls back to a
#: shell-wide ``OPENAI_BASE_URL`` set for another tool.
OPENAI_BASE_URL = "https://api.openai.com/v1"

SCHEMA_INSTRUCTION = "Reply with one JSON object and nothing else. It must conform to this JSON Schema:\n"


class ChatMessage(TypedDict):
    """One chat message."""

    role: Literal["system", "user", "assistant"]
    content: str


class LLMRequestTimeoutError(RuntimeError):
    """A request exceeded ``request_timeout_seconds``."""


class LLMReplyError(RuntimeError):
    """A reply that arrived but cannot be used, and would not improve if re-asked."""

    def __init__(self, message: str, content: str) -> None:
        super().__init__(message)
        self.content = content


class LLMTruncatedError(LLMReplyError):
    """The reply stopped at the output token limit (``finish_reason == "length"``)."""


class LLMRefusalError(LLMReplyError):
    """The model refused, or the provider filtered the reply."""


class LLMConfigurationError(RuntimeError):
    """The deployment, not the request, is at fault: no key, no model, a bad key,
    an unknown model, or a parameter the model refuses. Every other request
    would fail the same way, so callers should stop rather than skip."""


class JsonCompletion(BaseModel):
    """The parsed reply to one :meth:`LLMClient.acomplete_json` request."""

    data: dict[str, Any] = Field(description="The parsed JSON object.")
    content: str = Field(description="Raw text of the reply that parsed.")
    mode: Mode = Field(description="Response format that reply was requested in.")
    cached: bool = Field(description="Whether that reply came from the cache.")
    attempts: int = Field(description="Requests made, counting re-asks.")
    usage: TokenUsage = Field(
        default_factory=TokenUsage, description="Tokens over all attempts."
    )


# Partitioned per event loop: an asyncio.Semaphore binds to the loop it first
# waits on, so one process-wide instance breaks a second asyncio.run.
_inflight_semaphores: weakref.WeakKeyDictionary[Any, dict[int, asyncio.Semaphore]] = (
    weakref.WeakKeyDictionary()
)


def _inflight_semaphore(max_inflight: int) -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    per_loop = _inflight_semaphores.setdefault(loop, {})
    if max_inflight not in per_loop:
        per_loop[max_inflight] = asyncio.Semaphore(max_inflight)
    return per_loop[max_inflight]


def _backoff_seconds(attempt: int) -> float:
    capped = min(
        RETRY_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)), RETRY_BACKOFF_MAX_SECONDS
    )
    return capped * random.uniform(0.5, 1.0)


#: A rejected request that names the *request* — a value, an argument, a model
#: or a key — and will be rejected identically every time.
_CONFIG_MESSAGE_MARKERS = (
    "does not support",
    "unsupported parameter",
    "unsupported value",
    "unsupported_value",
    "unrecognized request argument",
    "unknown_parameter",
    "unknown field",
    "model_not_found",
    "does not exist or you do not have access",
    "invalid_api_key",
    "incorrect api key",
)
#: A rejected request that names the *input*: this episode, not the deployment.
_UNIT_FAULT_MARKERS = (
    "context_length_exceeded",
    "maximum context length",
    "string too long",
    "too many tokens",
)
#: A rejection of structured output itself, which ``auto`` answers by falling
#: back to ``json_object``.
_SCHEMA_REJECTION_MARKERS = (
    "response_format",
    "json_schema",
    "structured output",
)


def _mentions(error: BaseException, markers: Sequence[str]) -> bool:
    text = str(error).lower()
    return any(marker in text for marker in markers)


def _is_configuration_fault(error: BaseException) -> bool:
    return _mentions(error, _CONFIG_MESSAGE_MARKERS) and not _mentions(
        error, _UNIT_FAULT_MARKERS
    )


def _unusable(entry: CachedResponse, model: str) -> LLMReplyError | None:
    """Why *entry* cannot be used as it stands, or None."""
    if entry.refusal or entry.finish_reason == "content_filter":
        reason = entry.refusal or "the provider filtered the reply"
        return LLMRefusalError(f"{model} refused: {reason}", entry.content)
    if entry.finish_reason == "length":
        return LLMTruncatedError(
            f"{model} stopped at the output token limit after "
            f"{len(entry.content)} characters",
            entry.content,
        )
    return None


def _reask_message(error: LLMJsonParseError, content: str) -> ChatMessage:
    return {
        "role": "user",
        "content": (
            f"Your previous reply could not be parsed. {error}\n"
            f"Previous reply (excerpt):\n{feedback_excerpt(content, error)}\n\n"
            "Reply again with one JSON object only, following the format exactly."
        ),
    }


class LLMClient:
    """A JSON-object client over ``openai.AsyncOpenAI``.

    Args:
        settings: The endpoint and model. Defaults to the environment.
        cache: The response cache. Defaults to the environment's cache settings.
        http_client: An ``httpx.AsyncClient`` for the SDK, e.g. with a mock
            transport in tests.
    """

    def __init__(
        self,
        settings: LLMSettings | None = None,
        cache: LLMCache | None = None,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings or LLMSettings()
        self.cache = cache if cache is not None else LLMCache()
        self.usage = UsageTracker()
        self._http_client = http_client
        self._provider_client: openai.AsyncOpenAI | None = None
        self._json_schema_rejected = False
        # Set by the first provider reply; a cache hit does not count.
        self._reached = False

    @classmethod
    def from_settings(
        cls,
        settings: SchemaCastSettings,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> LLMClient:
        """Build a client and its cache from top-level settings."""
        return cls(settings.llm, LLMCache(settings.cache), http_client=http_client)

    def _charge(self, apply: Callable[[UsageTracker], None]) -> None:
        apply(self.usage)
        scoped = active_tracker()
        if scoped is not None and scoped is not self.usage:
            apply(scoped)

    def _count(self, *counters: str, n: int = 1) -> None:
        def _apply(tracker: UsageTracker) -> None:
            for counter in counters:
                tracker.incr(counter, n)

        self._charge(_apply)

    def _provider(self) -> openai.AsyncOpenAI:
        if self._provider_client is None:
            key = self.settings.api_key
            if key is None or not key.get_secret_value():
                raise LLMConfigurationError(
                    "No API key: set SCHEMACAST_LLM_API_KEY (any value for a local "
                    "endpoint). Without one only cached responses are served."
                )
            self._provider_client = openai.AsyncOpenAI(
                api_key=key.get_secret_value(),
                base_url=self.settings.base_url or OPENAI_BASE_URL,
                timeout=self.settings.request_timeout_seconds,
                max_retries=self.settings.transport_retries,
                http_client=self._http_client,
            )
        return self._provider_client

    def _mode(self) -> Mode:
        requested = self.settings.response_format
        if requested == "json_object" or (
            requested == "auto" and self._json_schema_rejected
        ):
            return "json_object"
        return "json_schema"

    def _request(
        self,
        messages: list[ChatMessage],
        json_schema: dict[str, Any],
        schema_name: str,
        mode: Mode,
    ) -> dict[str, Any]:
        wire: list[ChatMessage] = list(messages)
        if mode == "json_object":
            schema_text = json.dumps(json_schema, separators=(",", ":"))
            wire.append({"role": "system", "content": SCHEMA_INSTRUCTION + schema_text})
            response_format: dict[str, Any] = {"type": "json_object"}
        else:
            response_format = {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "schema": json_schema,
                    "strict": True,
                },
            }
        request: dict[str, Any] = {
            "model": self.settings.model,
            "messages": wire,
            "response_format": response_format,
        }
        if self.settings.temperature is not None:
            request["temperature"] = self.settings.temperature
        if self.settings.reasoning_effort is not None:
            request["reasoning_effort"] = self.settings.reasoning_effort
        if self.settings.max_output_tokens is not None:
            # OpenAI's reasoning models refuse `max_tokens`; other compatible
            # servers do not all read `max_completion_tokens`.
            name = (
                "max_completion_tokens"
                if self.settings.base_url is None
                else "max_tokens"
            )
            request[name] = self.settings.max_output_tokens
        return request

    async def _call(
        self,
        provider: openai.AsyncOpenAI,
        messages: list[ChatMessage],
        json_schema: dict[str, Any],
        schema_name: str,
    ) -> CachedResponse:
        while True:
            mode = self._mode()
            request = self._request(messages, json_schema, schema_name, mode)
            try:
                async with _inflight_semaphore(self.settings.max_inflight):
                    response = await provider.chat.completions.create(**request)
            except openai.APITimeoutError as error:
                self._count("calls_failed", "timeouts")
                raise LLMRequestTimeoutError(
                    f"Request exceeded {self.settings.request_timeout_seconds}s "
                    f"({self.settings.model})"
                ) from error
            except openai.APIConnectionError as error:
                self._count("calls_failed")
                if not self._reached:
                    # Never reached in this run: the endpoint is wrong or down,
                    # so every other request would fail the same way.
                    raise LLMConfigurationError(
                        f"Cannot reach {self.settings.base_url or OPENAI_BASE_URL}: "
                        f"{error}"
                    ) from error
                raise
            except (openai.BadRequestError, openai.UnprocessableEntityError) as error:
                if (
                    mode == "json_schema"
                    and self.settings.response_format == "auto"
                    and _mentions(error, _SCHEMA_REJECTION_MARKERS)
                    and not _mentions(error, _UNIT_FAULT_MARKERS)
                ):
                    self._json_schema_rejected = True
                    self._count("json_schema_fallbacks")
                    logger.warning(
                        "%s rejected json_schema output; using json_object for "
                        "the rest of this run: %s",
                        self.settings.model,
                        error,
                    )
                    continue
                self._count("calls_failed")
                if _is_configuration_fault(error):
                    self._count("rejected")
                    raise LLMConfigurationError(
                        f"{self.settings.model} rejected the request: {error}"
                    ) from error
                raise
            except (
                openai.AuthenticationError,
                openai.PermissionDeniedError,
                openai.NotFoundError,
            ) as error:
                self._count("calls_failed", "rejected")
                raise LLMConfigurationError(
                    f"{self.settings.model} at {self.settings.base_url or 'OpenAI'} "
                    f"rejected the request: {error}"
                ) from error
            except openai.RateLimitError:
                self._count("calls_failed", "rate_limited")
                raise
            except openai.APIError:
                self._count("calls_failed")
                raise

            self._reached = True
            choice = response.choices[0] if response.choices else None
            content = (choice.message.content or "") if choice is not None else ""
            refusal = choice.message.refusal if choice is not None else None
            usage = (
                token_usage_from_openai(response.usage)
                if response.usage is not None
                else TokenUsage()
            )
            sent = sum(len(message["content"]) for message in request["messages"])
            self._charge(
                partial(
                    UsageTracker.add_call,
                    chars_sent=sent,
                    chars_received=len(content),
                    usage=usage,
                )
            )
            return CachedResponse(
                content=content,
                mode=mode,
                model=response.model,
                finish_reason=choice.finish_reason if choice is not None else None,
                refusal=refusal or None,
                usage=None if usage.is_empty() else usage,
            )

    async def _complete_raw(
        self,
        messages: list[ChatMessage],
        json_schema: dict[str, Any],
        schema_name: str,
        *,
        backoff_attempt: int = 0,
        stage: Stage | None = None,
        source_id: str | None = None,
    ) -> tuple[CachedResponse, bool]:
        if not self.settings.model:
            raise LLMConfigurationError("No model: set SCHEMACAST_LLM_MODEL.")
        request = cache_request(messages, json_schema, self.settings)
        key = request_key(request)
        stored = StoredRequest(
            key=key, stage=stage, source_id=source_id, request=request
        )
        hit = await self.cache.aget(key)
        if hit is not None:
            self._charge(lambda t: t.add_hit(hit.usage))
            await self.cache.arecord(stored)
            return hit, True
        if self.cache.require_hit:
            raise await self.cache.amiss(stored)
        provider = self._provider()
        if backoff_attempt:
            await asyncio.sleep(_backoff_seconds(backoff_attempt))
        entry = await self._call(provider, messages, json_schema, schema_name)
        await self.cache.aset(key, entry, stored)
        return entry, False

    async def acomplete_json(
        self,
        messages: Sequence[ChatMessage],
        json_schema: dict[str, Any],
        *,
        schema_name: str = "instance",
        stage: Stage | None = None,
        source_id: str | None = None,
    ) -> JsonCompletion:
        """Ask for one JSON object conforming to *json_schema*.

        *stage* and *source_id* say who asks; they are stored beside the cached
        response and explain a miss, and never take part in the key.

        The reply is parsed, not validated: conformance to the schema is the
        caller's check. A reply that is not a JSON object is re-asked up to
        ``parse_retries`` times with the error shown, and given up on early
        when the same syntax error repeats. A timeout is re-issued once. A truncated
        or refused reply is not re-asked.

        Raises:
            LLMJsonParseError: no attempt produced a JSON object.
            LLMConfigurationError: no key or model, the endpoint refuses the
                configuration, or it cannot be reached before any reply.
            LLMRequestTimeoutError: a request timed out twice.
            LLMTruncatedError: the reply stopped at the output token limit.
            LLMRefusalError: the model refused or the provider filtered the reply.
            CacheMissError: a miss while the cache requires hits.
            openai.APIError: any other provider failure, after SDK retries.
        """
        base = list(messages)
        conversation = base
        total = TokenUsage()
        attempts = self.settings.parse_retries + 1
        last_error_msg: str | None = None
        timeout_reissue_available = True

        for attempt in range(attempts):
            try:
                entry, cached = await self._complete_raw(
                    conversation,
                    json_schema,
                    schema_name,
                    backoff_attempt=attempt,
                    stage=stage,
                    source_id=source_id,
                )
            except LLMRequestTimeoutError:
                if not timeout_reissue_available:
                    raise
                timeout_reissue_available = False
                logger.warning("Request timed out; re-issuing once")
                entry, cached = await self._complete_raw(
                    conversation,
                    json_schema,
                    schema_name,
                    backoff_attempt=1,
                    stage=stage,
                    source_id=source_id,
                )
            if entry.usage is not None:
                total = total + entry.usage
            # Re-asking would hit the same limit or the same refusal.
            unusable = _unusable(entry, self.settings.model)
            if unusable is not None:
                self._count(
                    "truncated"
                    if isinstance(unusable, LLMTruncatedError)
                    else "refused"
                )
                raise unusable

            try:
                data, fixes = parse_json_object(entry.content)
            except LLMJsonParseError as error:
                repeated = error.msg == last_error_msg
                last_error_msg = error.msg
                if repeated or attempt == attempts - 1:
                    self._count("parse_abandoned")
                    logger.error(
                        "Giving up after %d attempt(s): %s", attempt + 1, error.msg
                    )
                    raise
                self._count("parse_retries")
                logger.warning(
                    "Reply %d/%d is not a JSON object (%s); re-asking",
                    attempt + 1,
                    attempts,
                    error.msg,
                )
                conversation = [*base, _reask_message(error, entry.content)]
                continue

            if fixes:
                self._count("json_repairs", n=fixes)
            return JsonCompletion(
                data=data,
                content=entry.content,
                mode=entry.mode,
                cached=cached,
                attempts=attempt + 1,
                usage=total,
            )

        raise AssertionError("unreachable")
