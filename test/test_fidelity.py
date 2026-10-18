"""A coerced value is checked against the quote it came from."""

import pytest
from graflo.architecture.schema.vertex import Field

from schemacast.fidelity import value_mismatch

FLOAT = Field(name="power", type="FLOAT")
INT = Field(name="count", type="INT")
DATE = Field(name="since", type="DATETIME")
TEXT = Field(name="name", type="STRING")
FLOATS = Field(name="readings", type="LIST", item_type="FLOAT")


@pytest.mark.parametrize(
    ("field", "value", "quote"),
    [
        (FLOAT, 37.5, "draws 37.5 kW"),
        (INT, 1250, "rated to 1,250 °C"),
        (INT, 1250, "1.250 Stück"),
        (INT, 1250, "1 250 °C"),
        (INT, 1250, "1 250"),
        (FLOAT, 3.75, "3,75 kW"),
        (INT, 1200, "about 1.2k units"),
        (INT, 3_000_000, "3 million users"),
        (FLOAT, 0.45, "45% load"),
        (FLOAT, 45.0, "45% load"),
        (FLOAT, -5.0, "−5 °C"),
        (INT, 2, "priority 2"),
        (INT, 3, "three pumps"),
        (FLOATS, [1.5, 2.0], "1.5 and 2 bar"),
        (DATE, "2024-03-04", "4 March 2024"),
        (DATE, "2024-03-04", "on 2024-03-04 at noon"),
        (DATE, "2024-03-04", "March 4th, 2024"),
        (DATE, "2024-03-04", "04/03/24"),
        (DATE, "2024-03", "since 2024-03"),
        (DATE, "2024-03", "Mar 2024"),
        (DATE, "2024-03-04", "4 March"),
        (DATE, "2024-03-04", "le 4 mars 2024"),
        (DATE, "2024-03-04T10:30:00", "4 March 2024, 10:30"),
        (DATE, "2024-03-03", "yesterday"),
        (TEXT, "S-12", "press S-7"),
    ],
)
def test_a_value_the_quote_states_passes(field, value, quote):
    assert value_mismatch(value, field, quote) is None


@pytest.mark.parametrize(
    ("field", "value", "quote"),
    [
        (FLOAT, 37.5, "3.75 kW"),
        (FLOAT, 3750.0, "3.75 kW"),
        (INT, 1250, "125 °C"),
        (INT, 3, "priority 2"),
        (FLOATS, [1.5, 2.5], "1.5 and 2 bar"),
        (DATE, "2024-03-05", "4 March 2024"),
        (DATE, "2024-04-04", "4 March 2024"),
        (DATE, "2023-03-04", "4 March 2024"),
        (DATE, "2024-03-01", "March 2024"),
        (DATE, "2024-05-04", "2024-03-04"),
        (DATE, "2024-05-04", "on 2024-03-04 at noon"),
    ],
)
def test_a_value_the_quote_does_not_state_is_named(field, value, quote):
    message = value_mismatch(value, field, quote)
    assert message is not None and field.name in message
