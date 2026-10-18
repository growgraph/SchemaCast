"""Stage A selection: the slice of one schema a text is about.

Two mechanisms, kept apart. **Seeds** come from retrieval: lexical lanes over
the schema's element text, fused by rank. **Structure** comes from closure:
graflo's ``subschema()`` grows the seeds into a valid standalone schema under
a budget and never drops identity fields. A schema already under the budget
is passed whole.

Lanes:

- ``exact`` — code-like names and synonyms (``PLC``, ``WorkOrder``, an IRI)
  found verbatim, case-sensitively, in the text.
- ``lexical`` — names and synonyms found as phrases in the text after
  case-folding, camel/snake splitting and a light suffix stemmer, weighted by how few
  elements share the phrase; plus description words the text repeats.
- ``unit`` — a number followed by a token that is a property's declared unit
  (``200 kV``) seeds that property's owner.

A hit on a property seeds its owner — the vertex or edge — at half weight.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Literal

from graflo.architecture.graph_types.identifiers import EdgeId
from graflo.architecture.schema.context import (
    Budget,
    ElementText,
    ElisionReport,
    estimate_tokens,
    iter_elements,
    subschema,
)
from graflo.architecture.schema.document import Schema
from pydantic import BaseModel, ConfigDict, Field

Lane = Literal["exact", "lexical", "unit"]
LANES: tuple[Lane, ...] = ("exact", "lexical", "unit")
Seed = str | EdgeId

#: Weight of a property hit relative to a hit on the element itself.
PROPERTY_HIT_WEIGHT = 0.5
#: Description overlap counts at this fraction of a name match, and only from
#: this many shared words up, so one common word is no evidence.
DESCRIPTION_WEIGHT = 0.25
DESCRIPTION_MIN_WORDS = 2
MAX_PHRASE_WORDS = 8

_WORD = re.compile(r"[A-Za-z0-9]+")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_RAW_TOKEN = re.compile(r"[^\s,;:()\[\]{}\"'!?]+")
_NUMBER_UNIT = re.compile(
    r"\d+(?:[.,]\d+)?\s*(?:[-–−±]\s*\d+(?:[.,]\d+)?\s*)?"
    r"(?P<token>[%‰°]|°[A-Za-z]|[A-Za-zµμΩ][A-Za-z0-9µμΩ/·⁻²³%°]{0,11})"
)
_STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "has",
        "have",
        "in",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "per",
        "that",
        "the",
        "this",
        "to",
        "was",
        "were",
        "with",
    ]
)


class SelectionConfig(BaseModel):
    """Tuning for stage A selection."""

    model_config = ConfigDict(extra="forbid")

    max_tokens: int | None = Field(
        default=4000, ge=1, description="Token budget of the slice."
    )
    max_elements: int | None = Field(
        default=60, ge=1, description="Vertex and edge budget of the slice."
    )
    lane_depth: int = Field(
        default=20, ge=1, description="Hits kept per lane before fusion."
    )
    max_seeds: int = Field(default=12, ge=1, description="Seeds passed to closure.")
    rank_constant: float = Field(
        default=0.0,
        ge=0.0,
        description=(
            "Added to each rank before the reciprocal. Raising it lets "
            "agreement across lanes outweigh position within one."
        ),
    )
    min_relative_score: float = Field(
        default=0.2,
        gt=0.0,
        le=1.0,
        description=(
            "A seed must reach this fraction of the score a rank-1 hit in one "
            "lane earns. Relative, so it means the same for any schema."
        ),
    )
    lane_weights: dict[Lane, float] = Field(
        default_factory=lambda: dict.fromkeys(LANES, 1.0),
        description="Weight of each lane in fusion.",
    )

    @property
    def budget(self) -> Budget:
        return Budget(max_tokens=self.max_tokens, max_elements=self.max_elements)


class SeedHit(BaseModel):
    """A seed and how it was found."""

    vertex: str | None = Field(default=None, description="Seed vertex type.")
    edge_id: EdgeId | None = Field(default=None, description="Seed edge.")
    score: float = Field(description="Fused score.")
    ranks: dict[Lane, int] = Field(description="Rank within each lane that found it.")

    @property
    def seed(self) -> Seed:
        if self.edge_id is not None:
            return self.edge_id
        assert self.vertex is not None
        return self.vertex


class RetrievalMetrics(BaseModel):
    """What retrieval found, before and after each cut.

    ``fused == 0`` means nothing matched; ``fused > 0`` with no seeds would mean
    the gate rejected everything — kept apart because they have different fixes.
    """

    lane_hits: dict[Lane, int] = Field(description="Distinct seeds per lane.")
    fused: int = Field(description="Distinct seeds over all lanes.")
    gated_out: int = Field(description="Seeds below the relevance gate.")
    capped: int = Field(description="Seeds over max_seeds.")
    seeds: int = Field(description="Seeds passed on.")
    schema_tokens: int = Field(description="Estimated size of the whole schema.")


class Selection(BaseModel):
    """The schema a cast runs against, and how it was chosen."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    slice: Schema = Field(description="A valid standalone schema.")
    mode: Literal["whole", "slice"] = Field(
        description="``whole``: the schema fit the budget and was passed as is."
    )
    seeds: list[SeedHit] = Field(description="Seeds, best first.")
    elision: ElisionReport | None = Field(
        default=None, description="What closure left out; None when whole."
    )
    metrics: RetrievalMetrics


class SelectionError(RuntimeError):
    """No slice could be chosen.

    Attributes:
        reason: ``empty_text`` or ``no_match`` (the schema is over budget and
            nothing in the text matched it).
        metrics: Retrieval metrics, when retrieval ran.
    """

    def __init__(
        self,
        message: str,
        *,
        reason: Literal["empty_text", "no_match"],
        metrics: RetrievalMetrics | None = None,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.metrics = metrics


_DOUBLED = re.compile(r"([b-df-hj-km-rtv-y])\1$")
_SIBILANT_ES = re.compile(r"(?:[sxz]|[cs]h)es$")


def _drop_e(word: str) -> str:
    if len(word) > 3 and word.endswith("e") and not word.endswith("ee"):
        return word[:-1]
    return word


def _stem(word: str) -> str:
    """A light suffix stemmer: plural ``-s``/``-es``/``-ies``, ``-ed``, ``-ing``,
    and a final ``-e``, so ``route``, ``routes``, ``routed`` and ``routing``
    meet. Not linguistic: schema terms and text go through the same rules, and
    a stem shorter than three letters is never taken."""
    if len(word) <= 3:
        return word
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    for suffix in ("ing", "ed"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            base = word[: -len(suffix)]
            if _DOUBLED.search(base):
                base = base[:-1]
            return _drop_e(base)
    if _SIBILANT_ES.search(word):
        return word[:-2]
    if word.endswith("s") and not word.endswith("ss"):
        return _drop_e(word[:-1])
    return _drop_e(word)


def words(text: str) -> list[str]:
    """Case-folded, camel- and snake-split, stemmed words."""
    out: list[str] = []
    for token in _WORD.findall(text):
        for part in _CAMEL.split(token):
            if part:
                out.append(_stem(part.lower()))
    return out


_STEMMED_STOPWORDS = frozenset(_STOPWORDS | {_stem(word) for word in _STOPWORDS})


def _local_name(iri: str) -> str:
    return re.split(r"[#/:]", iri.rstrip("/#"))[-1]


def _looks_like_code(term: str) -> bool:
    """A term written as an identifier rather than a word."""
    if not term or any(ch.isspace() for ch in term) or not 2 <= len(term) <= 32:
        return False
    if term.lower() in _STOPWORDS:
        return False
    has_digit = any(ch.isdigit() for ch in term)
    mixed_case = any(ch.isupper() for ch in term[1:]) and any(
        ch.islower() for ch in term
    )
    symbol = any(not ch.isalnum() for ch in term)
    short_caps = term.isalpha() and term.isupper() and len(term) <= 5
    return has_digit or mixed_case or symbol or short_caps


def _boundary_find(needle: str, text: str) -> bool:
    start = text.find(needle)
    while start != -1:
        end = start + len(needle)
        if (start == 0 or not text[start - 1].isalnum()) and (
            end == len(text) or not text[end].isalnum()
        ):
            return True
        start = text.find(needle, start + 1)
    return False


def _seed_of(row: ElementText) -> Seed:
    if row.edge_id is not None:
        return row.edge_id
    return row.vertex if row.kind == "property" and row.vertex else row.name


def _seed_sort_key(seed: Seed) -> tuple[int, str, str, str]:
    if isinstance(seed, str):
        return (0, seed, "", "")
    return (1, seed[0], seed[1], seed[2] or "")


def _row_weight(row: ElementText) -> float:
    return PROPERTY_HIT_WEIGHT if row.kind == "property" else 1.0


def _descriptions(schema: Schema) -> dict[tuple[str, Seed, str], str]:
    core = schema.core_schema
    out: dict[tuple[str, Seed, str], str] = {}
    for vertex in core.vertex_config.vertices:
        out[("vertex", vertex.name, vertex.name)] = vertex.description or ""
        for field in vertex.properties:
            out[("property", vertex.name, field.name)] = field.description or ""
    for edge in core.edge_config.values():
        out[("edge", edge.edge_id, "")] = edge.description or ""
        for field in edge.properties:
            out[("property", edge.edge_id, field.name)] = field.description or ""
    return out


def _description_key(row: ElementText) -> tuple[str, Seed, str]:
    if row.kind == "property":
        return ("property", _seed_of(row), row.name)
    if row.kind == "edge":
        assert row.edge_id is not None
        return ("edge", row.edge_id, "")
    return ("vertex", row.name, row.name)


def _rank(scores: dict[Seed, float], depth: int) -> list[Seed]:
    ranked = sorted(
        (seed for seed, score in scores.items() if score > 0),
        key=lambda seed: (-scores[seed], _seed_sort_key(seed)),
    )
    return ranked[:depth]


def _exact_lane(rows: list[ElementText], text: str) -> dict[Seed, float]:
    tokens = set(_RAW_TOKEN.findall(text))
    scores: dict[Seed, float] = defaultdict(float)
    for row in rows:
        triggers = [term for term in row.terms if _looks_like_code(term)]
        matched = sum(
            1
            for trigger in triggers
            if trigger in tokens or _boundary_find(trigger, text)
        )
        matched += sum(1 for anchor in row.anchors if _boundary_find(anchor, text))
        if matched:
            seed = _seed_of(row)
            scores[seed] = max(scores[seed], matched * _row_weight(row))
    return scores


def _phrases(row: ElementText) -> set[tuple[str, ...]]:
    surface = [*row.terms, *(_local_name(anchor) for anchor in row.anchors)]
    return {
        phrase
        for phrase in (tuple(words(term)) for term in surface)
        if phrase and len(phrase) <= MAX_PHRASE_WORDS
    }


def _lexical_lane(
    rows: list[ElementText], text: str, descriptions: dict[tuple[str, Seed, str], str]
) -> dict[Seed, float]:
    text_words = words(text)
    grams = {
        tuple(text_words[i : i + n])
        for n in range(1, MAX_PHRASE_WORDS + 1)
        for i in range(len(text_words) - n + 1)
    }
    content_words = {word for word in text_words if word not in _STEMMED_STOPWORDS}

    row_phrases = [_phrases(row) for row in rows]
    frequency: dict[tuple[str, ...], int] = defaultdict(int)
    for phrases in row_phrases:
        for phrase in phrases:
            frequency[phrase] += 1

    scores: dict[Seed, float] = defaultdict(float)
    for row, phrases in zip(rows, row_phrases, strict=True):
        score = sum(
            (1.0 + 0.5 * (len(phrase) - 1)) / frequency[phrase]
            for phrase in phrases
            if phrase in grams
        )
        description = {
            word
            for word in words(descriptions.get(_description_key(row), ""))
            if word not in _STEMMED_STOPWORDS and len(word) > 2
        }
        shared = description & content_words
        if len(shared) >= DESCRIPTION_MIN_WORDS:
            score += DESCRIPTION_WEIGHT * len(shared) / len(description)
        if score:
            seed = _seed_of(row)
            scores[seed] = max(scores[seed], score * _row_weight(row))
    return scores


def number_adjacent_tokens(text: str) -> set[str]:
    """Tokens that directly follow a number: candidate unit mentions."""
    return {
        match.group("token")
        for match in _NUMBER_UNIT.finditer(text)
        if match.group("token").lower() not in _STOPWORDS
    }


def _unit_keys(token: str) -> set[str]:
    lowered = token.lower()
    return {lowered, _stem(lowered)}


def _unit_lane(rows: list[ElementText], text: str) -> dict[Seed, float]:
    found: set[str] = set()
    for token in number_adjacent_tokens(text):
        found |= _unit_keys(token)
    scores: dict[Seed, float] = {}
    for row in rows:
        if row.unit and _unit_keys(row.unit) & found:
            scores[_seed_of(row)] = 1.0
    return scores


def retrieve(
    schema: Schema, text: str, config: SelectionConfig | None = None
) -> tuple[list[SeedHit], RetrievalMetrics]:
    """Rank the schema's elements against *text*; return gated, capped seeds."""
    config = config or SelectionConfig()
    rows = list(iter_elements(schema))
    lanes: dict[Lane, list[Seed]] = {
        "exact": _rank(_exact_lane(rows, text), config.lane_depth),
        "lexical": _rank(
            _lexical_lane(rows, text, _descriptions(schema)), config.lane_depth
        ),
        "unit": _rank(_unit_lane(rows, text), config.lane_depth),
    }

    fused: dict[Seed, float] = defaultdict(float)
    ranks: dict[Seed, dict[Lane, int]] = defaultdict(dict)
    for lane, ranked in lanes.items():
        weight = config.lane_weights.get(lane, 0.0)
        for rank, seed in enumerate(ranked, start=1):
            fused[seed] += weight / (config.rank_constant + rank)
            ranks[seed][lane] = rank

    top_single = max(config.lane_weights.values(), default=1.0) / (
        config.rank_constant + 1
    )
    gate = config.min_relative_score * top_single
    ordered = sorted(fused, key=lambda seed: (-fused[seed], _seed_sort_key(seed)))
    passed = [seed for seed in ordered if fused[seed] >= gate]
    kept = passed[: config.max_seeds]

    hits = [
        SeedHit(
            vertex=seed if isinstance(seed, str) else None,
            edge_id=None if isinstance(seed, str) else seed,
            score=fused[seed],
            ranks=ranks[seed],
        )
        for seed in kept
    ]
    metrics = RetrievalMetrics(
        lane_hits={lane: len(ranked) for lane, ranked in lanes.items()},
        fused=len(fused),
        gated_out=len(ordered) - len(passed),
        capped=len(passed) - len(kept),
        seeds=len(kept),
        schema_tokens=estimate_tokens(schema.to_minimal_canonical_dict()),
    )
    return hits, metrics


def _fits(schema: Schema, metrics: RetrievalMetrics, budget: Budget) -> bool:
    core = schema.core_schema
    elements = len(core.vertex_config.vertices) + len(list(core.edge_config.values()))
    return (
        budget.max_tokens is None or metrics.schema_tokens <= budget.max_tokens
    ) and (budget.max_elements is None or elements <= budget.max_elements)


def select(
    schema: Schema, text: str, config: SelectionConfig | None = None
) -> Selection:
    """Choose the schema a cast of *text* runs against.

    Raises:
        SelectionError: the text is empty, or the schema is over budget and
            nothing in the text matched it.
    """
    config = config or SelectionConfig()
    if not text.strip():
        raise SelectionError("The text is empty.", reason="empty_text")
    hits, metrics = retrieve(schema, text, config)
    budget = config.budget
    if _fits(schema, metrics, budget):
        return Selection(slice=schema, mode="whole", seeds=hits, metrics=metrics)
    if not hits:
        raise SelectionError(
            "Nothing in the text matched the schema, and the schema is too large "
            f"to pass whole (~{metrics.schema_tokens} tokens).",
            reason="no_match",
            metrics=metrics,
        )
    sliced, elision = subschema(schema, [hit.seed for hit in hits], budget=budget)
    return Selection(
        slice=sliced, mode="slice", seeds=hits, elision=elision, metrics=metrics
    )
