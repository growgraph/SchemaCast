"""The oracle: a model that replies with the expected graph.

It goes through the whole pipeline — selection, validation, repair, resolution
— so its row is the ceiling the rest of the pipeline allows: a type selection
leaves out of the slice costs the oracle too. It never follows an injected
instruction and costs nothing. Asked to pick, it answers with the expected
pick, which the bench writes into its script before the call.
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
from schemacast.pick import NONE, PICK_SCHEMA_NAME, PickItem, offers

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


def oracle_pick_reply(
    item: PickItem, wanted: dict[str, dict[str, Any] | None]
) -> dict[str, Any]:
    """The pick reply a perfect model gives: per mention ref, the expected key."""

    def _same(a: dict[str, Any], b: dict[str, Any]) -> bool:
        return {k: str(v) for k, v in a.items()} == {k: str(v) for k, v in b.items()}

    picks = []
    for offer in offers(item):
        if not offer.candidates:
            continue
        key = wanted.get(offer.mention.ref)
        cid = next(
            (
                cid
                for cid, candidate in offer.candidates.items()
                if key is not None and _same(candidate.key, key)
            ),
            NONE,
        )
        picks.append({"mention": offer.mid, "candidate": cid})
    return {"picks": picks}


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


def oracle_client(
    expected: dict[str, ExpectedEpisode], picks: dict[str, str] | None = None
) -> LLMClient:
    """A client answering every request about an episode with its expected graph,
    and a pick request with the episode's entry of *picks*, read at request time.

    Uncached: the oracle is free and its replies are not model output.
    """
    scripted = picks if picks is not None else {}
    replies = {
        source_id: json.dumps(oracle_reply(item))
        for source_id, item in expected.items()
    }

    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        shape = body.get("response_format", {}).get("json_schema", {})
        script = scripted if shape.get("name") == PICK_SCHEMA_NAME else replies
        for message in body["messages"]:
            found = _EPISODE_ID.search(message["content"])
            if found and found.group(1) in script:
                return httpx.Response(200, json=_completion(script[found.group(1)]))
        return httpx.Response(
            400, json={"error": {"message": "unknown episode", "code": None}}
        )

    return LLMClient(
        LLMSettings(model=ORACLE, api_key=ORACLE, base_url="http://oracle.invalid/v1"),
        LLMCache(CacheSettings(enabled=False)),
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
    )
