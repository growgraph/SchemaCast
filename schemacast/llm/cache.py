"""On-disk cache of model responses, keyed by the logical request.

The key covers everything that changes a response — the messages, the JSON
Schema asked for, and every model setting that reaches the provider — and
nothing that does not, so a rerun of the same casts hits and a changed prompt
misses. Entries are written atomically, one JSON file per key.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from schemacast.config import CacheSettings, LLMSettings
from schemacast.llm.usage import TokenUsage

logger = logging.getLogger(__name__)

#: Bumped whenever the entry shape or the set of key inputs changes, so stale
#: entries miss instead of being read under the wrong assumptions.
CACHE_FORMAT_VERSION = 1
LLM_CACHE_SUBDIR = "llm"


class CacheMissError(RuntimeError):
    """A request is not cached and the cache is set to require hits."""


class CachedResponse(BaseModel):
    """One stored response."""

    content: str = Field(description="Response text as received.")
    mode: Literal["json_schema", "json_object"] = Field(
        description="Response format the request was sent with."
    )
    model: str | None = Field(default=None, description="Model the endpoint reported.")
    finish_reason: str | None = Field(default=None, description="Provider stop reason.")
    refusal: str | None = Field(
        default=None, description="The model's refusal message, when it refused."
    )
    usage: TokenUsage | None = Field(
        default=None, description="Token counts, replayed on a hit."
    )


def _running_under_pytest() -> bool:
    return "PYTEST_CURRENT_TEST" in os.environ or "pytest" in sys.modules


def default_cache_dir() -> Path:
    """``./.test_cache`` under pytest; else the user cache directory."""
    if _running_under_pytest():
        return Path.cwd() / ".test_cache"
    cache_home = os.environ.get("XDG_CACHE_HOME")
    if cache_home:
        return Path(cache_home) / "schemacast"
    if os.name == "nt":
        return Path.home() / "AppData" / "Local" / "schemacast"
    return Path.home() / ".cache" / "schemacast"


def cache_key_config(settings: LLMSettings) -> dict[str, Any]:
    """The model settings that take part in the key — the single definition."""
    return {
        "cache_format_version": CACHE_FORMAT_VERSION,
        "base_url": settings.base_url,
        "model": settings.model,
        "temperature": settings.temperature,
        "reasoning_effort": settings.reasoning_effort,
        "max_output_tokens": settings.max_output_tokens,
        "response_format": settings.response_format,
    }


def cache_key(
    messages: Sequence[Mapping[str, Any]],
    json_schema: dict[str, Any],
    settings: LLMSettings,
) -> str:
    """SHA-256 of the logical request.

    Keyed on the messages the caller passed, not on the wire request: a
    ``json_object`` fallback adds a schema message, and a replay must hit
    whichever format served the original run.
    """
    payload = {
        "messages": [dict(message) for message in messages],
        "json_schema": json_schema,
        "config": cache_key_config(settings),
    }
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class LLMCache:
    """Response cache under ``<dir>/llm/<key>.json``."""

    def __init__(self, settings: CacheSettings | None = None) -> None:
        self.settings = settings or CacheSettings()
        base = self.settings.dir or default_cache_dir()
        self.dir = Path(base).expanduser() / LLM_CACHE_SUBDIR
        if self.settings.enabled:
            self.dir.mkdir(parents=True, exist_ok=True)

    @property
    def enabled(self) -> bool:
        return self.settings.enabled

    @property
    def require_hit(self) -> bool:
        return self.settings.require_hit

    @property
    def writable(self) -> bool:
        return self.settings.enabled and not self.settings.read_only

    def path(self, key: str) -> Path:
        return self.dir / f"{key}.json"

    def get(self, key: str) -> CachedResponse | None:
        """The cached response for *key*, or None on a miss or unreadable entry."""
        if not self.enabled:
            return None
        path = self.path(key)
        if not path.exists():
            return None
        try:
            with open(path, encoding="utf-8") as handle:
                return CachedResponse.model_validate(json.load(handle))
        except (OSError, ValueError) as error:
            logger.warning("Unreadable cache entry %s: %s", path, error)
            return None

    def set(self, key: str, entry: CachedResponse) -> None:
        """Store *entry* atomically; a no-op when the cache is not writable."""
        if not self.writable:
            return
        path = self.path(key)
        handle, tmp_name = tempfile.mkstemp(
            dir=path.parent, prefix=f"{path.stem}.", suffix=".tmp"
        )
        tmp = Path(tmp_name)
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as out:
                json.dump(entry.model_dump(), out, indent=2, ensure_ascii=False)
            os.replace(tmp, path)
        except (OSError, TypeError, ValueError) as error:
            logger.warning("Could not write cache entry %s: %s", path, error)
            tmp.unlink(missing_ok=True)

    async def aget(self, key: str) -> CachedResponse | None:
        """:meth:`get` off the event loop."""
        return await asyncio.to_thread(self.get, key)

    async def aset(self, key: str, entry: CachedResponse) -> None:
        """:meth:`set` off the event loop."""
        await asyncio.to_thread(self.set, key, entry)

    def clear(self) -> int:
        """Delete every entry; returns how many were removed."""
        removed = 0
        if self.dir.exists():
            for path in self.dir.glob("*.json"):
                path.unlink(missing_ok=True)
                removed += 1
        return removed
