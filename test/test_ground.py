"""Quote location: offsets computed by SchemaCast, never taken from the model."""

import pytest

from schemacast.ground import locate

TEXT = "Ann  operates\nPress S-7, rated 200 kW."


def _at(span):
    return TEXT[span.start : span.end]


def test_exact_quote():
    span = locate("rated 200 kW", TEXT)
    assert span is not None and _at(span) == "rated 200 kW"


def test_case_and_whitespace_runs_are_ignored():
    span = locate("ann operates press s-7", TEXT)
    assert span is not None
    assert _at(span) == "Ann  operates\nPress S-7"


def test_surrounding_punctuation_is_ignored():
    span = locate('"200 kW."', TEXT)
    assert span is not None and _at(span) == "200 kW"


@pytest.mark.parametrize("quote", ["", "   ", "400 kW", "Bob operates"])
def test_absent_quotes_are_not_found(quote):
    assert locate(quote, TEXT) is None


def test_offsets_survive_characters_that_fold_to_two():
    text = "Straße 5, İzmir"
    span = locate("izmir", text)
    assert span is not None
    assert text[span.start : span.end] == "İzmir"


@pytest.mark.parametrize(
    ("quote", "text", "expected"),
    [
        ('"replace seal"', "order “replace seal” opened", "“replace seal”"),
        ("Ann's press", "Ann’s press", "Ann’s press"),
        ("S-7", "press S–7 stopped", "S–7"),
        ("-5 °C", "at −5 °C", "−5 °C"),
    ],
)
def test_typographic_quotes_and_dashes_fold(quote, text, expected):
    span = locate(quote, text)
    assert span is not None and text[span.start : span.end] == expected


def test_the_occurrence_nearest_the_owner_wins():
    text = "Press S-7 runs. Later, press S-9 was rated 200 kW; S-7 was not."
    first = locate("S-7", text)
    assert first is not None and first.start == text.index("S-7")
    owner = locate("S-7 was not", text)
    near = locate("S-7", text, near=owner)
    assert near is not None and near.start == text.rindex("S-7")


def test_a_tie_goes_to_the_first_occurrence():
    text = "ab x ab"
    span = locate("ab", text, near=locate("x", text))
    assert span is not None and span.start == 0
