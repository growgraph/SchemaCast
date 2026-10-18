"""Token and call accounting.

Tokens a provider billed are kept apart from tokens replayed out of the
response cache, so a cached rerun never reads as spend and a cost column still
says what a fresh run would cost.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from pydantic import BaseModel, Field


class TokenUsage(BaseModel):
    """Token counts for one call, as far as the provider reports them.

    ``None`` means *not reported*, which is not zero.
    """

    input_tokens: int | None = Field(default=None, description="Prompt tokens.")
    output_tokens: int | None = Field(default=None, description="Completion tokens.")
    reasoning_tokens: int | None = Field(
        default=None, description="Thinking tokens, counted inside output_tokens."
    )
    cache_read_input_tokens: int | None = Field(
        default=None,
        description=(
            "Prompt tokens served from the provider's prompt cache, counted "
            "inside input_tokens. Unrelated to SchemaCast's response cache."
        ),
    )

    def is_empty(self) -> bool:
        """True when the provider reported nothing."""
        return all(value is None for value in self.model_dump().values())

    def __add__(self, other: TokenUsage) -> TokenUsage:
        def _sum(a: int | None, b: int | None) -> int | None:
            return None if a is None and b is None else (a or 0) + (b or 0)

        return TokenUsage(
            **{
                name: _sum(getattr(self, name), getattr(other, name))
                for name in TokenUsage.model_fields
            }
        )


def _get(source: Any, key: str) -> Any:
    if isinstance(source, dict):
        return source.get(key)
    return getattr(source, key, None)


def _opt_int(source: Any, key: str) -> int | None:
    value = _get(source, key)
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


def token_usage_from_openai(usage: Any) -> TokenUsage:
    """Parse an OpenAI-shaped ``usage`` object, an SDK model or a dict."""
    prompt_tokens = _opt_int(usage, "prompt_tokens")
    completion_tokens = _opt_int(usage, "completion_tokens")
    if prompt_tokens is None or completion_tokens is None:
        return TokenUsage()
    return TokenUsage(
        input_tokens=prompt_tokens,
        output_tokens=completion_tokens,
        reasoning_tokens=_opt_int(
            _get(usage, "completion_tokens_details"), "reasoning_tokens"
        ),
        cache_read_input_tokens=_opt_int(
            _get(usage, "prompt_tokens_details"), "cached_tokens"
        ),
    )


class UsageTracker(BaseModel):
    """Calls, events and tokens for a run or a scope within it."""

    calls: int = Field(default=0, description="Provider calls that returned.")
    cache_hits: int = Field(default=0, description="Responses served from cache.")
    calls_failed: int = Field(default=0, description="Provider calls that raised.")
    timeouts: int = Field(default=0, description="Subset of calls_failed.")
    rate_limited: int = Field(default=0, description="Subset of calls_failed.")
    rejected: int = Field(
        default=0, description="Subset of calls_failed: configuration faults."
    )
    truncated: int = Field(
        default=0, description="Replies cut off at the output token limit."
    )
    refused: int = Field(default=0, description="Replies the model refused.")
    parse_retries: int = Field(default=0, description="Re-asks after bad JSON.")
    parse_abandoned: int = Field(default=0, description="Requests given up on.")
    json_repairs: int = Field(default=0, description="Bracket-kind repairs.")
    json_schema_fallbacks: int = Field(
        default=0, description="Switches from json_schema to json_object."
    )
    chars_sent: int = Field(default=0, description="Prompt characters sent.")
    chars_received: int = Field(default=0, description="Response characters.")
    billed: TokenUsage = Field(
        default_factory=TokenUsage, description="Tokens from live provider calls."
    )
    replayed: TokenUsage = Field(
        default_factory=TokenUsage, description="Tokens of responses served from cache."
    )

    def incr(self, counter: str, n: int = 1) -> None:
        """Add *n* to an integer counter."""
        setattr(self, counter, getattr(self, counter) + n)

    def add_call(self, chars_sent: int, chars_received: int, usage: TokenUsage) -> None:
        """Charge one live provider call."""
        self.calls += 1
        self.chars_sent += chars_sent
        self.chars_received += chars_received
        self.billed = self.billed + usage

    def add_hit(self, usage: TokenUsage | None) -> None:
        """Charge one cache hit."""
        self.cache_hits += 1
        if usage is not None:
            self.replayed = self.replayed + usage


_active_tracker: ContextVar[UsageTracker | None] = ContextVar(
    "schemacast_active_usage_tracker", default=None
)


@contextmanager
def track_usage(tracker: UsageTracker | None = None) -> Generator[UsageTracker]:
    """Charge calls made in this block (and tasks it spawns) to *tracker* too.

    Task-local: concurrent episodes each charge their own tracker.
    """
    tracker = tracker if tracker is not None else UsageTracker()
    token = _active_tracker.set(tracker)
    try:
        yield tracker
    finally:
        _active_tracker.reset(token)


def active_tracker() -> UsageTracker | None:
    """The tracker bound by the innermost :func:`track_usage`, if any."""
    return _active_tracker.get()
