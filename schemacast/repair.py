"""The typed repair prompt: the findings of one reply, grouped by kind.

Each kind gets one instruction and the fragments it concerns, nothing else.
The model is asked for the whole reply again, corrected, so nothing has to be
merged back. Consequences of other findings (an edge left out because its
vertex was) are not listed; they clear when the cause does.
"""

from __future__ import annotations

import json

from schemacast.llm.client import ChatMessage
from schemacast.validate import FINDING_ORDER, Finding, FindingKind

GUIDANCE: dict[FindingKind, str] = {
    "unknown_type": "Use only types listed under TYPES. If no type fits, move the item to `unmapped`.",
    "duplicate_ref": "Give every vertex its own ref.",
    "identity_insufficient": (
        "Give the key properties (`id:`) the text states. If the text does not state "
        "them, leave the vertex out or move it to `unmapped`; never invent a value."
    ),
    "undeclared_edge": (
        "Use only the edge patterns listed under TYPES, with `from` of the source type "
        "and `to` of the target type. If none fits, move the relation to `unmapped`."
    ),
    "dangling_ref": "`from` and `to` must be refs of vertices in your reply.",
    "undeclared_property": "Use only the properties listed for that type.",
    "duplicate_property": "Give each property at most once per vertex or edge.",
    "coercion": (
        "Write each value so it fits the property's type: a plain number for INT and "
        "FLOAT (1250, not 1,250), true or false for BOOL, an ISO 8601 date for "
        "DATETIME (2024-03-04, or 2024-03 or 2024 when the text gives no more). Leave "
        "the property out if the text does not state it."
    ),
    "value_not_in_quote": (
        "The value must be the one its `quote` states: the same digits, and the same "
        "day, month and year for a date. Correct the value or the quote; if the text "
        "does not state the value, leave the property out."
    ),
    "quote_not_found": (
        "Every `quote` must be copied character for character from the TEXT. Do not "
        "paraphrase, shorten with ellipses or quote CONTEXT, except for a key of an "
        "entity the TEXT refers to."
    ),
    "endpoint_invalid": "",
}

#: Added to a kind's guidance when one of its findings has this detail.
DETAIL_GUIDANCE: dict[tuple[FindingKind, str], str] = {
    ("coercion", "unit_mismatch"): (
        "A property shown with a unit in [brackets] takes its value in exactly that "
        "unit, and the symbol is case-sensitive (mW is not MW). If the text states the "
        "value in another unit, leave the property out; never convert it."
    ),
}

INTRO = (
    "# CORRECTIONS\n\n"
    "Your reply has the problems listed below, grouped by kind. Reply again with the "
    "whole extraction in the same format, with these fixed and everything else kept "
    "as it was. Remove an item, or move it to `unmapped`, when the text does not "
    "support it."
)


def repair_prompt(findings: list[Finding]) -> str:
    """The corrections message for *findings*. Deterministic for a given list."""
    by_kind: dict[FindingKind, list[Finding]] = {}
    for finding in findings:
        by_kind.setdefault(finding.kind, []).append(finding)
    sections = [INTRO]
    for kind in FINDING_ORDER:
        group = by_kind.get(kind)
        if not group or not GUIDANCE[kind]:
            continue
        lines = [f"## {kind}", "", GUIDANCE[kind], ""]
        details = sorted({f.detail for f in group if f.detail is not None})
        extra = [
            DETAIL_GUIDANCE[(kind, d)] for d in details if (kind, d) in DETAIL_GUIDANCE
        ]
        if extra:
            lines[-1:-1] = ["", *extra]
        for finding in group:
            fragment = json.dumps(
                finding.fragment, ensure_ascii=False, separators=(",", ":")
            )
            lines.append(f"- `{finding.path}`: {finding.message}\n  {fragment}")
        sections.append("\n".join(lines))
    return "\n\n".join(sections) + "\n"


def repair_messages(
    messages: list[ChatMessage], previous_reply: str, findings: list[Finding]
) -> list[ChatMessage]:
    """The original request, the reply that failed, and the corrections."""
    return [
        *messages,
        {"role": "assistant", "content": previous_reply},
        {"role": "user", "content": repair_prompt(findings)},
    ]
