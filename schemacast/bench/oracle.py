"""The oracle: a model that replies with the expected graph.

It goes through the whole pipeline — selection, validation, repair, resolution
— so its row is the ceiling the rest of the pipeline allows: a type selection
leaves out of the slice costs the oracle too. It never follows an injected
instruction and costs nothing.
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from schemacast.bench.corpus import ExpectedEpisode, ExpectedProp
from schemacast.config import CacheSettings, LLMSettings
from schemacast.llm.cache import LLMCache
from schemacast.llm.client import LLMClient

ORACLE = "oracle"

_EPISODE_ID = re.compile(r"<<<EPISODE id=(\S+) tag=")


def _props(props: dict[str, ExpectedProp]) -> list[dict[str, Any]]:
    return [
        {"name": name, "value": prop.value, "quote": prop.evidence}
        for name, prop in props.items()
    ]


def oracle_reply(expected: ExpectedEpisode) -> dict[str, Any]:
    """The flat reply a perfect model would give."""
    return {
        "vertices": [
            {
                "ref": handle,
                "type": item.type,
                "quote": item.quote,
                "mention": item.mention,
                "props": _props(item.props),
            }
            for handle, item in expected.vertices.items()
        ],
        "edges": [
            {
                "relation": edge.relation,
                "from": edge.from_,
                "to": edge.to,
                "quote": edge.quote,
                "props": _props(edge.props),
            }
            for edge in expected.edges
        ],
        "unmapped": [],
    }


def _completion(content: str) -> dict[str, Any]:
    return {
        "id": "oracle",
        "object": "chat.completion",
        "created": 0,
        "model": ORACLE,
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": content},
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


def oracle_client(expected: dict[str, ExpectedEpisode]) -> LLMClient:
    """A client answering every request about an episode with its expected graph.

    Uncached: the oracle is free and its replies are not model output.
    """
    replies = {
        source_id: json.dumps(oracle_reply(item))
        for source_id, item in expected.items()
    }

    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        for message in body["messages"]:
            found = _EPISODE_ID.search(message["content"])
            if found and found.group(1) in replies:
                return httpx.Response(200, json=_completion(replies[found.group(1)]))
        return httpx.Response(
            400, json={"error": {"message": "unknown episode", "code": None}}
        )

    return LLMClient(
        LLMSettings(model=ORACLE, api_key=ORACLE, base_url="http://oracle.invalid/v1"),
        LLMCache(CacheSettings(enabled=False)),
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
    )
