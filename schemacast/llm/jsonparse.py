"""Tolerant parsing of a model's JSON reply.

Repairs only what a model is known to break without changing meaning —
escaped delimiting quotes, ``//`` comments, trailing commas, a fenced block,
closing brackets of the wrong kind — then parses strictly. Anything else fails
loudly, with the decoder's position and the text around it, so a re-ask can
show the model exactly where its reply broke.
"""

from __future__ import annotations

import json
import re
from typing import Any

_JSON_COMMENT_RE = re.compile(r'"(?:[^"\\]|\\.)*"|//[^\n]*')
_TRAILING_COMMA_RE = re.compile(r",(\s*[}\]])")
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*(?:```|$)", re.DOTALL)

#: Context shown around a decode error, both directions.
JSON_ERROR_WINDOW = 150
#: Excerpt bounds for re-ask feedback.
FEEDBACK_BEFORE_ERROR = 600
FEEDBACK_AFTER_ERROR = 200
FEEDBACK_HEAD = 800
FEEDBACK_TAIL = 1200


class LLMJsonParseError(ValueError):
    """A reply that is not a JSON object, with the decoder's message and position."""

    def __init__(self, message: str, *, msg: str, pos: int, doc: str) -> None:
        super().__init__(message)
        self.msg = msg
        self.pos = pos
        self.doc = doc


def strip_json_comments(text: str) -> str:
    """Remove ``//`` line comments outside string literals."""

    def _replace(match: re.Match[str]) -> str:
        matched = match.group()
        return matched if matched.startswith('"') else ""

    return _JSON_COMMENT_RE.sub(_replace, text)


def strip_trailing_commas(text: str) -> str:
    """Remove commas directly before ``}`` or ``]``."""
    previous = None
    while previous != text:
        previous = text
        text = _TRAILING_COMMA_RE.sub(r"\1", text)
    return text


def unescape_json_delimiters(text: str) -> str:
    """Repair strings whose *delimiting* quotes were escaped (``"k": \\"v\\",``).

    A ``\\"`` where a value must start opens a string; inside such a string a
    ``\\"`` followed by a delimiter closes it. Escapes inside normally
    delimited strings are left alone. Outside strings, ``\\n``, ``\\t`` and
    ``\\r`` become the whitespace they denote.
    """
    out: list[str] = []
    i = 0
    n = len(text)
    in_string = False
    repaired_open = False
    last_significant = ""

    while i < n:
        ch = text[i]
        if in_string:
            if ch == "\\" and i + 1 < n:
                nxt = text[i + 1]
                if nxt == '"' and repaired_open:
                    j = i + 2
                    while j < n and text[j] in " \t\r\n":
                        j += 1
                    if j >= n or text[j] in ",}]:":
                        out.append('"')
                        i += 2
                        in_string = False
                        repaired_open = False
                        last_significant = '"'
                        continue
                out.append(ch)
                out.append(nxt)
                i += 2
                continue
            out.append(ch)
            if ch == '"':
                in_string = False
                repaired_open = False
                last_significant = '"'
            i += 1
            continue

        if (
            ch == "\\"
            and i + 1 < n
            and text[i + 1] == '"'
            and last_significant in (":", ",", "{", "[", "")
        ):
            out.append('"')
            i += 2
            in_string = True
            repaired_open = True
            continue

        if ch == "\\" and i + 1 < n and text[i + 1] in "nrt":
            out.append({"n": "\n", "r": "\r", "t": "\t"}[text[i + 1]])
            i += 2
            continue

        out.append(ch)
        if ch == '"':
            in_string = True
            repaired_open = False
        if not ch.isspace():
            last_significant = ch
        i += 1

    return "".join(out)


def sanitize_json_text(text: str) -> str:
    """Apply the meaning-preserving text repairs, in order."""
    return strip_trailing_commas(strip_json_comments(unescape_json_delimiters(text)))


def repair_bracket_kinds(text: str) -> tuple[str, int]:
    """Rewrite each closing bracket to the kind its opener demands.

    Only substitutes characters; never inserts, deletes or reorders. Gives up
    (returns the text unchanged and 0) on a closer with no open frame or on
    frames left open at the end — that is truncation, and closing it would
    invent a payload.

    Returns:
        The repaired text and the number of characters substituted.
    """
    out = list(text)
    stack: list[str] = []
    fixes = 0
    in_string = False
    i = 0
    n = len(text)

    while i < n:
        ch = text[i]
        if in_string:
            if ch == "\\":
                i += 2
                continue
            if ch == '"':
                in_string = False
            i += 1
            continue

        if ch == '"':
            in_string = True
        elif ch in "{[":
            stack.append(ch)
        elif ch in "}]":
            if not stack:
                return text, 0
            wanted = "}" if stack.pop() == "{" else "]"
            if wanted != ch:
                out[i] = wanted
                fixes += 1
        i += 1

    if stack:
        return text, 0
    return "".join(out), fixes


def _format_json_error(text: str, error: json.JSONDecodeError) -> str:
    start = max(0, error.pos - JSON_ERROR_WINDOW)
    end = min(len(text), error.pos + JSON_ERROR_WINDOW)
    return (
        f"Response is not valid JSON: {error.msg} at line {error.lineno} "
        f"column {error.colno} (char {error.pos}). Text around the error:\n"
        f"...{text[start:end]}..."
    )


def _loads_object(candidate: str) -> dict[str, Any]:
    parsed = json.loads(candidate, strict=False)
    if not isinstance(parsed, dict):
        raise LLMJsonParseError(
            "Response parsed as JSON but is not an object; got "
            f"{type(parsed).__name__}: {str(parsed)[:200]}",
            msg="not an object",
            pos=0,
            doc=candidate,
        )
    return parsed


def parse_json_object(text: str) -> tuple[dict[str, Any], int]:
    """Parse a reply as one JSON object.

    Strict parse first; then the first fenced block; then the bracket-kind
    repair. Raw control characters inside strings are tolerated.

    Returns:
        The object, and the number of brackets repaired (0 if none).

    Raises:
        LLMJsonParseError: with the strict error's position and context.
    """
    text = sanitize_json_text(text)
    try:
        return _loads_object(text), 0
    except json.JSONDecodeError as strict_error:
        reported = strict_error
        match = _JSON_FENCE_RE.search(text)
        if match and match.group(1):
            fenced = match.group(1)
            try:
                return _loads_object(fenced), 0
            except json.JSONDecodeError as fence_error:
                text, reported = fenced, fence_error

        repaired, fixes = repair_bracket_kinds(text)
        if fixes:
            try:
                return _loads_object(repaired), fixes
            except json.JSONDecodeError:
                pass

        raise LLMJsonParseError(
            _format_json_error(text, reported),
            msg=reported.msg,
            pos=reported.pos,
            doc=text,
        ) from strict_error


def feedback_excerpt(content: str, error: Exception) -> str:
    """A bounded slice of a failed reply to show the model when re-asking."""
    if isinstance(error, LLMJsonParseError) and error.pos:
        doc = error.doc or content
        start = max(0, error.pos - FEEDBACK_BEFORE_ERROR)
        end = min(len(doc), error.pos + FEEDBACK_AFTER_ERROR)
        prefix = "..." if start else ""
        suffix = "..." if end < len(doc) else ""
        return f"{prefix}{doc[start:end]}{suffix}"

    if len(content) <= FEEDBACK_HEAD + FEEDBACK_TAIL:
        return content
    elided = len(content) - FEEDBACK_HEAD - FEEDBACK_TAIL
    return (
        f"{content[:FEEDBACK_HEAD]}\n... [{elided} characters elided] ...\n"
        f"{content[-FEEDBACK_TAIL:]}"
    )
