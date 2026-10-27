"""The response cache: keys, atomic entries, stored requests, explained misses,
read-only and disabled modes."""

import json

from schemacast.config import CacheSettings, LLMSettings
from schemacast.llm.cache import (
    CachedResponse,
    CacheMissError,
    LLMCache,
    StoredRequest,
    cache_key,
    cache_key_config,
    cache_request,
    default_cache_dir,
    request_key,
)

SCHEMA = {"type": "object"}
MESSAGES = [{"role": "user", "content": "hi"}]


def _entry(content: str = "{}") -> CachedResponse:
    return CachedResponse(content=content, mode="json_schema")


def test_round_trip(tmp_path):
    cache = LLMCache(CacheSettings(dir=tmp_path))
    cache.set("k", _entry('{"a": 1}'))
    assert cache.get("k") == _entry('{"a": 1}')
    assert not list(cache.dir.glob("*.tmp"))


def test_read_only_and_disabled_write_nothing(tmp_path):
    read_only = LLMCache(CacheSettings(dir=tmp_path, read_only=True))
    read_only.set("k", _entry())
    assert read_only.get("k") is None

    disabled = LLMCache(CacheSettings(dir=tmp_path, enabled=False))
    disabled.set("k", _entry())
    assert disabled.get("k") is None


def test_unreadable_entry_is_a_miss(tmp_path):
    cache = LLMCache(CacheSettings(dir=tmp_path))
    cache.path("k").write_text("{not json", encoding="utf-8")
    assert cache.get("k") is None


def test_clear(tmp_path):
    cache = LLMCache(CacheSettings(dir=tmp_path))
    cache.set("a", _entry())
    cache.set("b", _entry())
    assert cache.clear() == 2
    assert cache.get("a") is None


def test_key_follows_what_changes_a_response():
    base = LLMSettings(model="m")
    key = cache_key(MESSAGES, SCHEMA, base)
    assert key == cache_key(MESSAGES, SCHEMA, base)
    assert key == cache_key(MESSAGES, SCHEMA, base.model_copy(update={"api_key": "s"}))
    assert key == cache_key(
        MESSAGES, SCHEMA, base.model_copy(update={"request_timeout_seconds": 5})
    )
    for changed in (
        {"model": "other"},
        {"temperature": 0.0},
        {"base_url": "http://x/v1"},
        {"response_format": "json_object"},
        {"max_output_tokens": 10},
    ):
        assert key != cache_key(MESSAGES, SCHEMA, base.model_copy(update=changed))
    assert key != cache_key(MESSAGES, {"type": "array"}, base)
    assert key != cache_key([{"role": "user", "content": "hi!"}], SCHEMA, base)


def test_default_dir_is_local_under_pytest():
    assert default_cache_dir().name == ".test_cache"


SETTINGS = LLMSettings(
    model="m",
    base_url="http://fake.local/v1",
    temperature=0.0,
    max_output_tokens=100,
    response_format="json_schema",
)


def _stored(content: str = "hi", **fields) -> StoredRequest:
    request = cache_request([{"role": "user", "content": content}], SCHEMA, SETTINGS)
    return StoredRequest(key=request_key(request), request=request, **fields)


def test_the_key_of_a_fixed_request_is_pinned():
    # Existing entries hit only while this holds.
    assert cache_key(MESSAGES, SCHEMA, SETTINGS) == (
        "b92ffe541a510351eed9990d937449c337dd026cedc92c5ccf14430028eeb0dd"
    )
    assert _stored().key == cache_key(MESSAGES, SCHEMA, SETTINGS)


def test_the_request_is_stored_beside_its_entry(tmp_path):
    cache = LLMCache(CacheSettings(dir=tmp_path))
    stored = _stored(stage="cast", source_id="note-1")
    cache.set(stored.key, _entry(), stored)

    data = json.loads(cache.request_path(stored.key).read_text(encoding="utf-8"))
    assert data == {
        "key": stored.key,
        "stage": "cast",
        "source_id": "note-1",
        "request": {
            "messages": MESSAGES,
            "json_schema": SCHEMA,
            "config": cache_key_config(SETTINGS),
        },
    }
    # Stage and source are not part of what is hashed.
    assert request_key(data["request"]) == stored.key
    assert cache.get(stored.key) == _entry()
    assert cache.clear() == 1
    assert not list(cache.dir.iterdir())


def test_read_only_writes_no_request_and_no_directory(tmp_path):
    cache = LLMCache(CacheSettings(dir=tmp_path, read_only=True))
    stored = _stored(stage="cast")
    cache.set(stored.key, _entry(), stored)
    cache.record(stored)
    assert not tmp_path.joinpath("llm").exists()


def test_record_adds_a_missing_request_only(tmp_path):
    cache = LLMCache(CacheSettings(dir=tmp_path))
    stored = _stored(stage="pick", source_id="a")
    cache.record(stored)
    cache.record(stored.model_copy(update={"source_id": "b"}))
    assert cache.stored_request(stored.key) == stored


def test_a_miss_names_the_nearest_request_of_its_stage_and_the_change(tmp_path):
    cache = LLMCache(CacheSettings(dir=tmp_path))
    old = _stored("Ann operates press S-7.\nIt runs at 200 kW.", stage="cast")
    other = _stored("Ann operates press S-7.\nIt runs at 200 kW.!", stage="pick")
    far = _stored("Something else entirely.", stage="cast")
    for stored in (old, other, far):
        cache.set(stored.key, _entry(), stored)

    missed = _stored(
        "Ann operates press S-7.\nIt runs at 250 kW.", stage="cast", source_id="n"
    )
    error = cache.miss(missed)

    assert isinstance(error, CacheMissError)
    assert (error.key, error.stage, error.source_id) == (missed.key, "cast", "n")
    assert error.nearest == old.key
    assert error.similarity is not None and 0 < error.similarity < 1
    assert error.diff is not None
    assert "-It runs at 200 kW." in error.diff
    assert "+It runs at 250 kW." in error.diff
    assert f"--- stored {old.key}" in error.diff
    assert "require hits" in str(error) and error.diff in str(error)
    assert error.as_dict()["nearest"] == old.key


def test_a_miss_prefers_a_stored_request_of_the_same_source(tmp_path):
    cache = LLMCache(CacheSettings(dir=tmp_path))
    close = _stored("text one", stage="cast", source_id="x")
    same = _stored("text two, rewritten", stage="cast", source_id="y")
    for stored in (close, same):
        cache.set(stored.key, _entry(), stored)
    error = cache.miss(_stored("text one!", stage="cast", source_id="y"))
    assert (error.nearest, error.nearest_source_id) == (same.key, "y")


def test_a_miss_without_stored_requests_says_so(tmp_path):
    cache = LLMCache(CacheSettings(dir=tmp_path))
    cache.set("old", _entry())
    error = cache.miss(_stored(stage="repair", source_id="n"))
    assert error.nearest is None and error.diff is None
    assert "No stored repair request to compare it with" in str(error)


def test_a_diff_is_truncated(tmp_path, monkeypatch):
    monkeypatch.setattr("schemacast.llm.cache.MAX_DIFF_LINES", 5)
    cache = LLMCache(CacheSettings(dir=tmp_path))
    old = _stored("\n".join(f"line {i}" for i in range(40)), stage="cast")
    cache.set(old.key, _entry(), old)
    error = cache.miss(
        _stored("\n".join(f"line {i}!" for i in range(40)), stage="cast")
    )
    assert error.diff is not None and error.diff.endswith("(diff truncated)")
    assert len(error.diff.splitlines()) == 6
