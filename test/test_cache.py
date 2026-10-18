"""The response cache: keys, atomic entries, read-only and disabled modes."""

from schemacast.config import CacheSettings, LLMSettings
from schemacast.llm.cache import CachedResponse, LLMCache, cache_key, default_cache_dir

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
