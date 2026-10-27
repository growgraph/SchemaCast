"""On-disk cache of model responses, keyed by the logical request.

The key covers everything that changes a response — the messages, the JSON
Schema asked for, and every model setting that reaches the provider — and
nothing that does not, so a rerun of the same casts hits and a changed prompt
misses. Entries are written atomically, one JSON file per key, each with the
request it answers beside it in ``<key>.request.json``: the hashed request plus
the stage and source that asked, which are not part of the key. A miss under
``require_hit`` is explained against the nearest stored request.
"""

from __future__ import annotations

import asyncio
import difflib
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
#: Suffix of the file beside an entry that holds the request it answers.
REQUEST_SUFFIX = ".request.json"

#: The step of a cast that made a request; recorded beside it, never keyed.
Stage = Literal["cast", "repair", "pick"]

#: Stored requests read to explain one miss, newest first.
MAX_SCANNED = 2000
#: Stored requests compared in full, after a cheap prefilter.
MAX_COMPARED = 16
#: Bounds of the diff a miss carries.
MAX_DIFF_LINES = 120
MAX_DIFF_CHARS = 8000


class StoredRequest(BaseModel):
    """The request a cache entry answers, with who asked for it."""

    key: str = Field(description="The entry's key: SHA-256 of ``request``.")
    stage: Stage | None = Field(default=None, description="The step that asked.")
    source_id: str | None = Field(
        default=None, description="The episode the request was for, when known."
    )
    request: dict[str, Any] = Field(
        description="What was hashed: ``messages``, ``json_schema``, ``config``."
    )


class CacheMissError(RuntimeError):
    """A request is not cached and the cache is set to require hits.

    Attributes:
        key: The key that missed.
        stage: The step that asked, when known.
        source_id: The episode it was for, when known.
        nearest: The key of the most similar stored request of the same stage.
        nearest_source_id: The episode that stored request was for.
        similarity: Line similarity of the two requests' messages, 0 to 1.
        diff: A unified diff from the nearest stored request to the missed one,
            truncated.
    """

    def __init__(
        self,
        message: str,
        *,
        key: str | None = None,
        stage: Stage | None = None,
        source_id: str | None = None,
        nearest: str | None = None,
        nearest_source_id: str | None = None,
        similarity: float | None = None,
        diff: str | None = None,
    ) -> None:
        super().__init__(message)
        self.key = key
        self.stage = stage
        self.source_id = source_id
        self.nearest = nearest
        self.nearest_source_id = nearest_source_id
        self.similarity = similarity
        self.diff = diff

    def as_dict(self) -> dict[str, Any]:
        """The attributes as JSON; the message is ``str(error)``."""
        return {
            "key": self.key,
            "stage": self.stage,
            "source_id": self.source_id,
            "nearest": self.nearest,
            "nearest_source_id": self.nearest_source_id,
            "similarity": self.similarity,
            "diff": self.diff,
        }


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


def cache_request(
    messages: Sequence[Mapping[str, Any]],
    json_schema: dict[str, Any],
    settings: LLMSettings,
) -> dict[str, Any]:
    """The logical request the key is taken over.

    The messages the caller passed, not the wire request: a ``json_object``
    fallback adds a schema message, and a replay must hit whichever format
    served the original run.
    """
    return {
        "messages": [dict(message) for message in messages],
        "json_schema": json_schema,
        "config": cache_key_config(settings),
    }


def request_key(request: Mapping[str, Any]) -> str:
    """SHA-256 of *request* as canonical JSON."""
    canonical = json.dumps(
        request, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def cache_key(
    messages: Sequence[Mapping[str, Any]],
    json_schema: dict[str, Any],
    settings: LLMSettings,
) -> str:
    """SHA-256 of the logical request (:func:`cache_request`)."""
    return request_key(cache_request(messages, json_schema, settings))


def _message_lines(request: Mapping[str, Any]) -> list[str]:
    lines: list[str] = []
    for message in request.get("messages") or []:
        lines.append(f"[{message.get('role')}]")
        lines.extend(str(message.get("content", "")).splitlines())
    return lines


def _request_lines(request: Mapping[str, Any]) -> list[str]:
    lines = _message_lines(request)
    for part in ("json_schema", "config"):
        lines.append(f"[{part}]")
        lines.extend(
            json.dumps(
                request.get(part), indent=1, sort_keys=True, ensure_ascii=False
            ).splitlines()
        )
    return lines


def request_diff(stored: StoredRequest, missed: StoredRequest) -> str:
    """A unified diff from *stored* to *missed*, truncated."""
    lines = list(
        difflib.unified_diff(
            _request_lines(stored.request),
            _request_lines(missed.request),
            fromfile=f"stored {stored.key}",
            tofile=f"missed {missed.key}",
            lineterm="",
            n=2,
        )
    )
    cut = len(lines) > MAX_DIFF_LINES
    text = "\n".join(lines[:MAX_DIFF_LINES])
    if len(text) > MAX_DIFF_CHARS:
        text, cut = text[:MAX_DIFF_CHARS], True
    return text + ("\n... (diff truncated)" if cut else "")


class LLMCache:
    """Response cache under ``<dir>/llm/<key>.json``, each entry's request
    beside it in ``<key>.request.json``."""

    def __init__(self, settings: CacheSettings | None = None) -> None:
        self.settings = settings or CacheSettings()
        base = self.settings.dir or default_cache_dir()
        self.dir = Path(base).expanduser() / LLM_CACHE_SUBDIR
        if self.writable:
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

    def request_path(self, key: str) -> Path:
        return self.dir / f"{key}{REQUEST_SUFFIX}"

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

    def _write(self, path: Path, data: Any) -> None:
        handle, tmp_name = tempfile.mkstemp(
            dir=path.parent, prefix=f"{path.name}.", suffix=".tmp"
        )
        tmp = Path(tmp_name)
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as out:
                json.dump(data, out, indent=2, ensure_ascii=False)
            os.replace(tmp, path)
        except (OSError, TypeError, ValueError) as error:
            logger.warning("Could not write cache file %s: %s", path, error)
            tmp.unlink(missing_ok=True)

    def set(
        self, key: str, entry: CachedResponse, request: StoredRequest | None = None
    ) -> None:
        """Store *entry*, and *request* beside it, atomically; a no-op when the
        cache is not writable."""
        if not self.writable:
            return
        if request is not None:
            self._write(self.request_path(key), request.model_dump(mode="json"))
        self._write(self.path(key), entry.model_dump())

    def record(self, request: StoredRequest) -> None:
        """Store *request* beside its entry when none is there yet; a no-op when
        the cache is not writable."""
        if self.writable and not self.request_path(request.key).exists():
            self._write(self.request_path(request.key), request.model_dump(mode="json"))

    def stored_request(self, key: str) -> StoredRequest | None:
        """The request stored beside entry *key*, or None."""
        path = self.request_path(key)
        try:
            with open(path, encoding="utf-8") as handle:
                return StoredRequest.model_validate(json.load(handle))
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as error:
            logger.warning("Unreadable cached request %s: %s", path, error)
            return None

    def nearest(self, missed: StoredRequest) -> tuple[StoredRequest, float] | None:
        """The stored request most like *missed*, by line similarity of the
        messages, and that similarity.

        Only requests of *missed*'s stage are compared, and of its source when
        any is stored. The newest :data:`MAX_SCANNED` are read; the
        :data:`MAX_COMPARED` that a cheap bound ranks highest are compared in
        full.
        """
        if not self.dir.is_dir():
            return None
        found: list[tuple[float, str]] = []
        for path in self.dir.glob(f"*{REQUEST_SUFFIX}"):
            try:
                found.append((-path.stat().st_mtime, path.name))
            except OSError:
                continue
        found.sort()
        stored: list[StoredRequest] = []
        for _, name in found[:MAX_SCANNED]:
            request = self.stored_request(name.removesuffix(REQUEST_SUFFIX))
            if request is None or request.key == missed.key:
                continue
            if missed.stage is None or request.stage == missed.stage:
                stored.append(request)
        same_source = [r for r in stored if r.source_id == missed.source_id]
        if missed.source_id is not None and same_source:
            stored = same_source
        if not stored:
            return None
        # seq2 is the missed request: the matcher indexes it once.
        matcher = difflib.SequenceMatcher(None, autojunk=False)
        matcher.set_seq2(_message_lines(missed.request))

        def quick(request: StoredRequest) -> float:
            matcher.set_seq1(_message_lines(request.request))
            return matcher.quick_ratio()

        def full(request: StoredRequest) -> float:
            matcher.set_seq1(_message_lines(request.request))
            return matcher.ratio()

        bounded = sorted(stored, key=lambda r: (-quick(r), r.key))[:MAX_COMPARED]
        scored = sorted(
            ((full(r), r) for r in bounded), key=lambda p: (-p[0], p[1].key)
        )
        similarity, best = scored[0]
        return best, similarity

    def miss(self, missed: StoredRequest) -> CacheMissError:
        """The error for a strict miss of *missed*, explained against the
        nearest stored request."""
        what = f"{missed.stage} request" if missed.stage else "request"
        source = f" for {missed.source_id!r}" if missed.source_id else ""
        message = (
            f"The cache is set to require hits and this {what}{source} is not "
            f"cached: key {missed.key} in {self.dir}."
        )
        fields: dict[str, Any] = {
            "key": missed.key,
            "stage": missed.stage,
            "source_id": missed.source_id,
        }
        if self.request_path(missed.key).exists():
            message += (
                " Its request is stored but its response is missing or unreadable."
            )
            return CacheMissError(message, **fields)
        found = self.nearest(missed)
        if found is None:
            return CacheMissError(
                message + f" No stored {what} to compare it with: entries cached "
                "without their request carry none.",
                **fields,
            )
        nearest, similarity = found
        diff = request_diff(nearest, missed)
        held = f" for {nearest.source_id!r}" if nearest.source_id else ""
        message += (
            f" Nearest stored {what}: key {nearest.key}{held}, similarity "
            f"{similarity:.3f}. Stored to missed:\n{diff}"
        )
        return CacheMissError(
            message,
            nearest=nearest.key,
            nearest_source_id=nearest.source_id,
            similarity=similarity,
            diff=diff,
            **fields,
        )

    async def aget(self, key: str) -> CachedResponse | None:
        """:meth:`get` off the event loop."""
        return await asyncio.to_thread(self.get, key)

    async def aset(
        self, key: str, entry: CachedResponse, request: StoredRequest | None = None
    ) -> None:
        """:meth:`set` off the event loop."""
        await asyncio.to_thread(self.set, key, entry, request)

    async def arecord(self, request: StoredRequest) -> None:
        """:meth:`record` off the event loop."""
        await asyncio.to_thread(self.record, request)

    async def amiss(self, missed: StoredRequest) -> CacheMissError:
        """:meth:`miss` off the event loop."""
        return await asyncio.to_thread(self.miss, missed)

    def clear(self) -> int:
        """Delete every entry and its request; returns how many entries were
        removed."""
        removed = 0
        if self.dir.exists():
            for path in self.dir.glob("*.json"):
                path.unlink(missing_ok=True)
                removed += not path.name.endswith(REQUEST_SUFFIX)
        return removed
