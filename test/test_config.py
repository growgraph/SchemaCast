"""Settings come from SCHEMACAST_* variables only."""

import pytest
from pydantic import ValidationError

from schemacast.config import (
    DEFAULT_MODEL,
    CacheSettings,
    LLMSettings,
    SchemaCastSettings,
)


def test_reads_its_own_prefixes(monkeypatch, tmp_path):
    monkeypatch.setenv("SCHEMACAST_LLM_MODEL", "qwen3:8b")
    monkeypatch.setenv("SCHEMACAST_LLM_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("SCHEMACAST_LLM_API_KEY", "local")
    monkeypatch.setenv("SCHEMACAST_CACHE_REQUIRE_HIT", "true")
    monkeypatch.setenv("SCHEMACAST_CACHE_DIR", str(tmp_path))

    settings = SchemaCastSettings()
    assert settings.llm.model == "qwen3:8b"
    assert settings.llm.base_url == "http://localhost:11434/v1"
    assert settings.llm.api_key is not None
    assert settings.llm.api_key.get_secret_value() == "local"
    assert settings.cache.require_hit
    assert settings.cache.dir == tmp_path


def test_ignores_other_tools_variables(monkeypatch):
    monkeypatch.setenv("LLM_MODEL_NAME", "gpt-4o")
    monkeypatch.setenv("LLM_API_KEY", "other")
    monkeypatch.setenv("OPENAI_API_KEY", "other")
    settings = LLMSettings()
    assert settings.model == DEFAULT_MODEL == "gpt-5.6-luna"
    assert settings.api_key is None


def test_key_is_not_printed():
    assert "sk-secret" not in repr(LLMSettings(api_key="sk-secret"))


def test_require_hit_needs_the_cache():
    with pytest.raises(ValidationError):
        CacheSettings(enabled=False, require_hit=True)
