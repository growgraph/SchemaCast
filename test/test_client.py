"""LLMClient.acomplete_json against a scripted endpoint."""

from __future__ import annotations

import asyncio

import httpx
import openai
import pytest
from conftest import MESSAGES, SCHEMA, completion, error

from schemacast.config import CacheSettings
from schemacast.llm import (
    CacheMissError,
    LLMConfigurationError,
    LLMJsonParseError,
    LLMRefusalError,
    LLMRequestTimeoutError,
    LLMTruncatedError,
    track_usage,
)
from schemacast.llm.cache import cache_key
from schemacast.llm.client import SCHEMA_INSTRUCTION

SCHEMA_REJECTED = error(
    400, "Invalid parameter: 'response_format' of type 'json_schema' is not supported"
)


async def test_requests_strict_json_schema_and_parses(make_client):
    client, endpoint = make_client({"answer": "press 7"})
    result = await client.acomplete_json(MESSAGES, SCHEMA, schema_name="probe")

    assert result.data == {"answer": "press 7"}
    assert result.mode == "json_schema"
    assert not result.cached
    assert result.attempts == 1
    request = endpoint.requests[0]
    assert request["model"] == "fake-model"
    assert request["messages"] == MESSAGES
    assert request["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "probe", "schema": SCHEMA, "strict": True},
    }
    assert "temperature" not in request
    assert client.usage.calls == 1
    assert client.usage.billed.input_tokens == 10


async def test_optional_settings_reach_the_request(make_client):
    client, endpoint = make_client(
        {"answer": "x"}, temperature=0.0, reasoning_effort="low", max_output_tokens=64
    )
    await client.acomplete_json(MESSAGES, SCHEMA)
    request = endpoint.requests[0]
    assert request["temperature"] == 0.0
    assert request["reasoning_effort"] == "low"
    assert request["max_tokens"] == 64


async def test_openai_gets_max_completion_tokens(make_client):
    client, endpoint = make_client({"answer": "x"}, base_url=None, max_output_tokens=64)
    await client.acomplete_json(MESSAGES, SCHEMA)
    assert endpoint.requests[0]["max_completion_tokens"] == 64


async def test_second_identical_request_is_served_from_cache(make_client):
    client, endpoint = make_client({"answer": "press 7"})
    first = await client.acomplete_json(MESSAGES, SCHEMA)
    second = await client.acomplete_json(MESSAGES, SCHEMA)

    assert len(endpoint.requests) == 1
    assert second.cached
    assert second.data == first.data
    assert client.usage.cache_hits == 1
    assert client.usage.replayed.input_tokens == 10
    assert client.usage.billed.input_tokens == 10


async def test_replay_with_require_hit_needs_no_key_and_makes_no_request(make_client):
    client, _ = make_client({"answer": "press 7"})
    original = await client.acomplete_json(MESSAGES, SCHEMA)

    replayer, endpoint = make_client(
        cache=CacheSettings(require_hit=True), api_key=None
    )
    replayed = await replayer.acomplete_json(MESSAGES, SCHEMA)

    assert endpoint.requests == []
    assert replayed.content == original.content
    assert replayed.data == original.data


async def test_require_hit_raises_on_a_miss(make_client):
    client, endpoint = make_client(
        {"answer": "x"}, cache=CacheSettings(require_hit=True)
    )
    with pytest.raises(CacheMissError):
        await client.acomplete_json(MESSAGES, SCHEMA)
    assert endpoint.requests == []


async def test_no_key_fails_closed(make_client):
    client, endpoint = make_client({"answer": "x"}, api_key=None)
    with pytest.raises(LLMConfigurationError, match="SCHEMACAST_LLM_API_KEY"):
        await client.acomplete_json(MESSAGES, SCHEMA)
    assert endpoint.requests == []


async def test_no_model_fails_closed(make_client):
    client, endpoint = make_client({"answer": "x"}, model="")
    with pytest.raises(LLMConfigurationError, match="SCHEMACAST_LLM_MODEL"):
        await client.acomplete_json(MESSAGES, SCHEMA)
    assert endpoint.requests == []


async def test_read_only_cache_writes_nothing(make_client):
    client, endpoint = make_client(
        {"answer": "x"}, {"answer": "x"}, cache=CacheSettings(read_only=True)
    )
    await client.acomplete_json(MESSAGES, SCHEMA)
    await client.acomplete_json(MESSAGES, SCHEMA)
    assert len(endpoint.requests) == 2


async def test_auto_falls_back_to_json_object_and_stays_there(make_client):
    client, endpoint = make_client(SCHEMA_REJECTED, {"answer": "a"}, {"answer": "b"})
    first = await client.acomplete_json(MESSAGES, SCHEMA)
    second = await client.acomplete_json([*MESSAGES, MESSAGES[1]], SCHEMA)

    assert first.mode == second.mode == "json_object"
    assert len(endpoint.requests) == 3
    fallback = endpoint.requests[1]
    assert fallback["response_format"] == {"type": "json_object"}
    assert fallback["messages"][: len(MESSAGES)] == MESSAGES
    assert fallback["messages"][-1]["content"].startswith(SCHEMA_INSTRUCTION)
    assert endpoint.requests[2]["response_format"] == {"type": "json_object"}
    assert client.usage.json_schema_fallbacks == 1
    assert client.usage.calls_failed == 0


async def test_explicit_json_schema_does_not_fall_back(make_client):
    client, _ = make_client(SCHEMA_REJECTED, response_format="json_schema")
    with pytest.raises(openai.BadRequestError):
        await client.acomplete_json(MESSAGES, SCHEMA)


async def test_json_object_mode_never_sends_a_schema(make_client):
    client, endpoint = make_client({"answer": "a"}, response_format="json_object")
    result = await client.acomplete_json(MESSAGES, SCHEMA)
    assert result.mode == "json_object"
    assert endpoint.requests[0]["response_format"] == {"type": "json_object"}


@pytest.mark.parametrize(
    "response",
    [
        error(401, "Incorrect API key provided", "invalid_api_key"),
        error(404, "The model `nope` does not exist", "model_not_found"),
        error(400, "Unsupported parameter: 'temperature' is not supported"),
    ],
)
async def test_configuration_faults_are_typed(make_client, response):
    client, _ = make_client(response)
    with pytest.raises(LLMConfigurationError):
        await client.acomplete_json(MESSAGES, SCHEMA)
    assert client.usage.rejected == 1


async def test_context_length_is_not_a_configuration_fault(make_client):
    client, _ = make_client(
        error(
            400, "This model's maximum context length is 8192 tokens; unsupported value"
        )
    )
    with pytest.raises(openai.BadRequestError):
        await client.acomplete_json(MESSAGES, SCHEMA)
    assert client.usage.rejected == 0


async def test_rate_limit_propagates_and_is_counted(make_client):
    client, _ = make_client(error(429, "Rate limit reached"))
    with pytest.raises(openai.RateLimitError):
        await client.acomplete_json(MESSAGES, SCHEMA)
    assert client.usage.rate_limited == 1


async def test_a_truncated_reply_is_not_reasked(make_client):
    cut = httpx.Response(
        200, json=completion('{"answer": "pre', finish_reason="length")
    )
    client, endpoint = make_client(cut, {"answer": "never asked"})
    with pytest.raises(LLMTruncatedError) as raised:
        await client.acomplete_json(MESSAGES, SCHEMA)
    assert raised.value.content == '{"answer": "pre'
    assert len(endpoint.requests) == 1
    assert client.usage.truncated == 1
    assert client.usage.parse_retries == 0


async def test_a_cached_truncated_reply_replays_the_same_error(make_client):
    cut = httpx.Response(
        200, json=completion('{"answer": "pre', finish_reason="length")
    )
    client, endpoint = make_client(cut)
    for _ in range(2):
        with pytest.raises(LLMTruncatedError):
            await client.acomplete_json(MESSAGES, SCHEMA)
    assert len(endpoint.requests) == 1
    assert client.usage.cache_hits == 1


@pytest.mark.parametrize(
    "body",
    [
        completion("", refusal="I can't help with that."),
        completion('{"answer": ""}', finish_reason="content_filter"),
    ],
)
async def test_a_refusal_is_typed_and_not_reasked(make_client, body):
    client, endpoint = make_client(httpx.Response(200, json=body), {"answer": "x"})
    with pytest.raises(LLMRefusalError):
        await client.acomplete_json(MESSAGES, SCHEMA)
    assert len(endpoint.requests) == 1
    assert client.usage.refused == 1


async def test_bad_json_is_reasked_with_the_error(make_client):
    client, endpoint = make_client('{"answer": "a" "b"}', {"answer": "fixed"})
    result = await client.acomplete_json(MESSAGES, SCHEMA)

    assert result.data == {"answer": "fixed"}
    assert result.attempts == 2
    reask = endpoint.requests[1]["messages"]
    assert reask[: len(MESSAGES)] == MESSAGES
    assert len(reask) == len(MESSAGES) + 1
    assert "could not be parsed" in reask[-1]["content"]
    assert client.usage.parse_retries == 1


async def test_repeated_syntax_error_is_abandoned_early(make_client):
    client, endpoint = make_client(
        '{"answer": "a" "b"}', '{"answer": "c" "d"}', {"answer": "x"}, parse_retries=3
    )
    with pytest.raises(LLMJsonParseError):
        await client.acomplete_json(MESSAGES, SCHEMA)
    assert len(endpoint.requests) == 2
    assert client.usage.parse_abandoned == 1


async def test_parse_retries_are_bounded(make_client):
    client, endpoint = make_client("[1]", "nope", parse_retries=1)
    with pytest.raises(LLMJsonParseError):
        await client.acomplete_json(MESSAGES, SCHEMA)
    assert len(endpoint.requests) == 2


async def test_tolerant_parse_and_bracket_repair_count(make_client):
    client, _ = make_client('```json\n{"answer": ["a", "b",}]\n```')
    result = await client.acomplete_json(MESSAGES, SCHEMA)
    assert result.data == {"answer": ["a", "b"]}
    assert client.usage.json_repairs == 2


async def test_timeout_is_reissued_once(make_client):
    client, endpoint = make_client(httpx.ReadTimeout("slow"), {"answer": "late"})
    result = await client.acomplete_json(MESSAGES, SCHEMA)
    assert result.data == {"answer": "late"}
    assert len(endpoint.requests) == 2
    assert client.usage.timeouts == 1


async def test_second_timeout_propagates(make_client):
    client, _ = make_client(httpx.ReadTimeout("slow"), httpx.ReadTimeout("slow"))
    with pytest.raises(LLMRequestTimeoutError):
        await client.acomplete_json(MESSAGES, SCHEMA)
    assert client.usage.timeouts == 2


async def test_usage_is_charged_to_the_active_scope(make_client):
    client, _ = make_client({"answer": "x"}, {"answer": "y"})
    await client.acomplete_json(MESSAGES, SCHEMA)
    with track_usage() as scoped:
        await client.acomplete_json([*MESSAGES, MESSAGES[0]], SCHEMA)
    assert scoped.calls == 1
    assert client.usage.calls == 2


def test_client_works_across_event_loops(make_client):
    """The in-flight limiter binds per loop, so a second asyncio.run must work."""
    client, endpoint = make_client({"answer": "x"}, {"answer": "y"}, max_inflight=1)
    asyncio.run(client.acomplete_json(MESSAGES, SCHEMA))
    asyncio.run(client.acomplete_json([*MESSAGES, MESSAGES[0]], SCHEMA))
    assert len(endpoint.requests) == 2


async def test_a_call_stores_its_request_with_stage_and_source(make_client):
    client, _ = make_client({"answer": "x"})
    await client.acomplete_json(MESSAGES, SCHEMA, stage="cast", source_id="note-1")
    key = cache_key(MESSAGES, SCHEMA, client.settings)
    stored = client.cache.stored_request(key)
    assert stored is not None
    assert (stored.stage, stored.source_id) == ("cast", "note-1")
    assert stored.request["messages"] == MESSAGES


async def test_a_hit_adds_a_missing_request_unless_read_only(make_client, tmp_path):
    client, _ = make_client({"answer": "x"})
    await client.acomplete_json(MESSAGES, SCHEMA)
    key = cache_key(MESSAGES, SCHEMA, client.settings)
    client.cache.request_path(key).unlink()

    reader, _ = make_client(cache=CacheSettings(read_only=True))
    await reader.acomplete_json(MESSAGES, SCHEMA, stage="pick")
    assert not reader.cache.request_path(key).exists()

    await client.acomplete_json(MESSAGES, SCHEMA, stage="pick", source_id="m")
    stored = client.cache.stored_request(key)
    assert stored is not None and stored.stage == "pick"


async def test_a_strict_miss_explains_itself(make_client):
    client, _ = make_client({"answer": "x"})
    await client.acomplete_json(MESSAGES, SCHEMA, stage="cast", source_id="s")
    changed = [MESSAGES[0], {**MESSAGES[1], "content": MESSAGES[1]["content"] + "!"}]

    replayer, endpoint = make_client(cache=CacheSettings(require_hit=True))
    with pytest.raises(CacheMissError) as caught:
        await replayer.acomplete_json(changed, SCHEMA, stage="cast", source_id="s")
    error = caught.value
    assert endpoint.requests == []
    assert (error.stage, error.source_id) == ("cast", "s")
    assert error.key == cache_key(changed, SCHEMA, replayer.settings)
    assert error.nearest == cache_key(MESSAGES, SCHEMA, client.settings)
    assert error.diff is not None and "!" in error.diff
