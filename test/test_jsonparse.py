"""Tolerant JSON parsing of model replies."""

import pytest

from schemacast.llm.jsonparse import (
    LLMJsonParseError,
    feedback_excerpt,
    parse_json_object,
    repair_bracket_kinds,
    strip_json_comments,
    strip_trailing_commas,
    unescape_json_delimiters,
)


def test_strict_object_parses_unchanged():
    assert parse_json_object('{"a": [1, 2]}') == ({"a": [1, 2]}, 0)


def test_fenced_block_is_extracted():
    assert parse_json_object('Here:\n```json\n{"a": 1}\n```\nDone.')[0] == {"a": 1}


def test_comments_outside_strings_are_dropped():
    text = '{"url": "http://x//y", // a comment\n "b": 2}'
    assert strip_json_comments(text) == '{"url": "http://x//y", \n "b": 2}'


def test_trailing_commas_are_dropped():
    assert strip_trailing_commas('{"a": [1, 2,], }') == '{"a": [1, 2] }'


def test_escaped_delimiters_are_repaired():
    text = '{"quote": \\"Ann said \\"hi\\" here\\", "b": 1}'
    assert parse_json_object(unescape_json_delimiters(text))[0]["b"] == 1


def test_raw_control_characters_inside_strings_are_tolerated():
    assert parse_json_object('{"a": "line\nbreak"}')[0] == {"a": "line\nbreak"}


def test_mismatched_bracket_kinds_are_repaired():
    data, fixes = parse_json_object('{"a": [1, 2}]')
    assert data == {"a": [1, 2]}
    assert fixes == 2


def test_truncation_is_not_repaired():
    assert repair_bracket_kinds('{"a": [1, 2') == ('{"a": [1, 2', 0)
    with pytest.raises(LLMJsonParseError):
        parse_json_object('{"a": [1, 2')


@pytest.mark.parametrize("text", ["", "[1, 2]", "42", "not json"])
def test_non_objects_raise(text):
    with pytest.raises(LLMJsonParseError):
        parse_json_object(text)


def test_error_carries_position_and_context():
    with pytest.raises(LLMJsonParseError) as info:
        parse_json_object('{"a": 1 "b": 2}')
    assert info.value.pos == 8
    assert "Text around the error" in str(info.value)


def test_feedback_excerpt_is_bounded():
    content = "x" * 10_000
    excerpt = feedback_excerpt(content, ValueError("schema"))
    assert len(excerpt) < 2_200
    assert "characters elided" in excerpt
