"""Is a coerced value what its quote states?

Checked for numbers written as digits and for dates; every other value passes.
A quote with no digit (``three pumps``, ``yesterday``) is not checked, since a
spelled-out or relative value cannot be read back without inference.

Numbers: the quote's digit runs are read with every plausible separator
reading (``1,250`` and ``1.250`` as grouping, ``3,75`` as a decimal comma), a
trailing ``k``/``M``/``bn`` or its word as a multiplier and ``%`` as a
hundredth; the value matches when its magnitude equals one reading. Units are
not converted: ``3.75 kW`` does not support ``3750``.

Dates: each part the value gives must be in the quote — the day as a number,
the month as a number or an English month name, the year as four or two
digits. A quote with no four-digit number leaves the year to context. A month
written in another language is not checked.
"""

from __future__ import annotations

import math
import re
from typing import Any

from graflo.architecture.schema.vertex import Field, FieldType

_NUMERIC = frozenset({FieldType.INT, FieldType.UINT, FieldType.FLOAT, FieldType.DOUBLE})

#: A run of digits joined by separators a number may be written with.
_RUN = re.compile(r"\d+(?:[.,'’   ]\d+)*")
_SEPARATORS = re.compile(r"[.,'’   ]")
_MULTIPLIER = re.compile(
    r"^\s?(?P<word>k|K|thousand|M|mn|million|bn|B|billion|%)(?![^\W\d_])"
)
_SCALE = {
    "k": 1e3,
    "K": 1e3,
    "thousand": 1e3,
    "M": 1e6,
    "mn": 1e6,
    "million": 1e6,
    "bn": 1e9,
    "B": 1e9,
    "billion": 1e9,
    "%": 1e-2,
}

_MONTHS: dict[str, int] = {}
for _number, _name in enumerate(
    (
        "january",
        "february",
        "march",
        "april",
        "may",
        "june",
        "july",
        "august",
        "september",
        "october",
        "november",
        "december",
    ),
    start=1,
):
    _MONTHS[_name] = _number
    _MONTHS[_name[:3]] = _number
_MONTHS["sept"] = 9
_WORD = re.compile(r"[^\W\d_]+")
_NUMERIC_DATE = re.compile(r"\d{4}-\d{1,2}|\d{1,2}[./-]\d{1,2}[./-]\d{2,4}")
_ISO_DATE = re.compile(r"^(?P<year>\d{4})(?:-(?P<month>\d{2})(?:-(?P<day>\d{2}))?)?")


def _readings(run: str) -> set[float]:
    """Every number *run* can be read as, ignoring sign."""
    parts = _SEPARATORS.split(run)
    seps = _SEPARATORS.findall(run)
    out: set[float] = set()
    if not seps:
        return {float(run)}
    # Every separator groups digits.
    out.add(float("".join(parts)))
    # The last separator is a decimal point or comma; the others group.
    if seps[-1] in ".,":
        out.add(float("".join(parts[:-1]) + "." + parts[-1]))
    # Plain spaces may also separate two numbers.
    if " " in seps:
        for piece in run.split(" "):
            if piece:
                out |= _readings(piece)
    return out


def _numbers(quote: str) -> set[float]:
    found: set[float] = set()
    for match in _RUN.finditer(quote):
        readings = _readings(match.group().strip())
        found |= readings
        scale = _MULTIPLIER.match(quote[match.end() :])
        if scale is not None:
            factor = _SCALE[scale.group("word")]
            found |= {value * factor for value in readings}
    return found


def _number_mismatch(value: Any, quote: str) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    magnitude = abs(float(value))
    return not any(
        math.isclose(magnitude, candidate, rel_tol=1e-9, abs_tol=1e-12)
        for candidate in _numbers(quote)
    )


def _date_mismatch(value: Any, quote: str) -> bool:
    if not isinstance(value, str):
        return False
    parts = _ISO_DATE.match(value)
    if parts is None:
        return False
    tokens = re.findall(r"\d+", quote)
    numbers = {int(token) for token in tokens}
    words = [word.casefold() for word in _WORD.findall(quote)]
    named = {_MONTHS[word] for word in words if word in _MONTHS}
    day, month, year = parts.group("day"), parts.group("month"), parts.group("year")
    if day is not None and int(day) not in numbers:
        return True
    if month is not None:
        # A month name, when given, decides; a date written in digits must
        # hold the number; otherwise the month may be named in another language.
        if named:
            if int(month) not in named:
                return True
        elif int(month) not in numbers and (_NUMERIC_DATE.search(quote) or not words):
            return True
    has_year = any(len(token) == 4 for token in tokens)
    if has_year and int(year) not in numbers:
        short = [t for t in tokens if len(t) == 2 and int(t) == int(year) % 100]
        if not short:
            return True
    return False


def value_mismatch(value: Any, field: Field, quote: str) -> str | None:
    """Why *value*, coerced for *field*, is not what *quote* states; None when
    it is, or when it cannot be checked."""
    if field.type is None or not re.search(r"\d", quote):
        return None
    ftype = FieldType(field.type)
    if ftype == FieldType.LIST:
        ftype = FieldType(field.item_type or FieldType.STRING)
        items = value if isinstance(value, list) else [value]
    else:
        items = [value]
    if ftype in _NUMERIC:
        bad = [item for item in items if _number_mismatch(item, quote)]
        if bad:
            return (
                f"{field.name}: {bad[0]!r} is not a number the quote {quote!r} states."
            )
    elif ftype == FieldType.DATETIME:
        bad = [item for item in items if _date_mismatch(item, quote)]
        if bad:
            return f"{field.name}: {bad[0]!r} has a date part the quote {quote!r} does not state."
    return None
