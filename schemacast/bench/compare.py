"""Paired comparison of two benchmark reports, episode by episode.

Episodes pair on case name and source id; only paired episodes enter the pooled
figures. Runs on different corpus versions pair only the cases whose content
digest is the same in both; a report written before cases carried digests
pairs by case name only when the caller says the cases are unchanged. Each headline rate is read off the pooled counts of either run, and
its difference gets a paired bootstrap interval: episodes are resampled with
their pair, so the interval reflects how much the difference depends on which
episodes the corpus happens to hold. The resampling is seeded, so a comparison
of the same two files is byte-identical.
"""

from __future__ import annotations

import random
from pathlib import Path

from pydantic import BaseModel, Field

from schemacast.bench.oracle import ORACLE
from schemacast.bench.report import RATES, _fmt, _table, case_score
from schemacast.bench.run import BenchReport, Row
from schemacast.bench.score import Counts

#: Rates compared, in report order.
HEADLINE: tuple[str, ...] = (
    "conformance",
    "conformance_final",
    "type_hallucination",
    "relation_hallucination",
    "property_hallucination",
    "ungrounded",
    "unfaithful_value",
    "entity_f1",
    "edge_f1",
    "property_f1",
    "direction",
    "span_accuracy",
    "tokens_per_episode",
    "requests_per_episode",
)
#: Decimals shown for a rate, where not two.
DIGITS: dict[str, int] = {"tokens_per_episode": 0}
#: Bootstrap resamples and seed.
RESAMPLES = 2000
SEED = 0
#: Episodes listed under the largest drops and rises.
TOP = 10
STATUSES = ("ok", "partial", "failed")


class CompareError(ValueError):
    """Two reports that cannot be paired, or an ambiguous row."""


class RateDelta(BaseModel):
    """One rate in both runs and its paired difference."""

    rate: str
    a: float | None
    b: float | None
    delta: float | None = Field(description="b − a.")
    low: float | None = Field(description="2.5th percentile of the bootstrap delta.")
    high: float | None = Field(description="97.5th percentile of the bootstrap delta.")


class EpisodeDelta(BaseModel):
    """One paired episode."""

    case: str
    source_id: str
    status_a: str
    status_b: str
    score_a: float | None
    score_b: float | None
    delta: float | None


class CompareReport(BaseModel):
    """Run B read against run A."""

    corpus_version: str
    corpus_digest: str
    corpus_version_b: str
    changed_cases: list[str] = Field(
        description="Cases in both runs whose content differs; left out."
    )
    paired_by_name: bool = Field(
        default=False,
        description="Corpora differ and cases were paired by name on the caller's word.",
    )
    row_a: str
    row_b: str
    split_a: str
    split_b: str
    paired: int
    only_a: list[str] = Field(description="``case/source_id`` only in run A.")
    only_b: list[str] = Field(description="``case/source_id`` only in run B.")
    resamples: int
    rates: list[RateDelta]
    wins: int = Field(description="Episodes whose score rose.")
    losses: int = Field(description="Episodes whose score fell.")
    ties: int
    unscored: int = Field(description="Episodes with no score in either run.")
    transitions: dict[str, dict[str, int]] = Field(
        description="Status in A → status in B → episodes."
    )
    drops: list[EpisodeDelta]
    rises: list[EpisodeDelta]


def load_report(path: str | Path) -> BenchReport:
    """A report written by ``schemacast-bench``."""
    return BenchReport.model_validate_json(Path(path).read_text(encoding="utf-8"))


def pick_row(report: BenchReport, label: str | None) -> Row:
    """The row named *label*; without one, the only row or the only model row.

    Raises:
        CompareError: no such row, or more than one candidate.
    """
    labels = [row.label for row in report.rows]
    if label is not None:
        for row in report.rows:
            if row.label == label:
                return row
        raise CompareError(f"no row {label!r}; rows: {', '.join(labels)}")
    if len(report.rows) == 1:
        return report.rows[0]
    models = [row for row in report.rows if row.label != ORACLE]
    if len(models) == 1:
        return models[0]
    raise CompareError(f"more than one row, name one: {', '.join(labels)}")


def _episodes(
    row: Row, cases: set[str] | None = None
) -> dict[tuple[str, str], tuple[str, Counts]]:
    return {
        (case.name, episode.source_id): (episode.status, episode.counts)
        for case in row.cases
        if cases is None or case.name in cases
        for episode in case.episodes
    }


def _comparable_cases(
    first: Row, second: Row, by_name: bool
) -> tuple[set[str], list[str]]:
    """Cases whose content digest matches in both rows, and those that differ.
    With *by_name*, a case without a digest on either side counts as unchanged."""
    left = {case.name: case.digest for case in first.cases}
    right = {case.name: case.digest for case in second.cases}
    same = {
        name
        for name in left.keys() & right.keys()
        if (left[name] is not None and left[name] == right[name])
        or (by_name and (left[name] is None or right[name] is None))
    }
    changed = sorted((left.keys() & right.keys()) - same)
    return same, changed


def _sum(vectors: list[tuple[int, ...]], picks: list[int]) -> Counts:
    sums = [sum(column) for column in zip(*(vectors[i] for i in picks), strict=True)]
    return Counts.model_construct(**dict(zip(Counts.model_fields, sums, strict=True)))


def _vector(counts: Counts) -> tuple[int, ...]:
    return tuple(getattr(counts, name) for name in Counts.model_fields)


def _percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    index = q * (len(ordered) - 1)
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)


def _delta(a: float | None, b: float | None) -> float | None:
    return None if a is None or b is None else b - a


def compare(
    a: BenchReport,
    b: BenchReport,
    *,
    row_a: str | None = None,
    row_b: str | None = None,
    resamples: int = RESAMPLES,
    pair_by_name: bool = False,
) -> CompareReport:
    """Read *b* against *a*, episode by episode.

    Raises:
        CompareError: the reports ran against different corpora with no case
            in common, a row is ambiguous or missing, or no episode pairs.
    """
    first, second = pick_row(a, row_a), pick_row(b, row_b)
    cases: set[str] | None = None
    changed: list[str] = []
    if a.corpus_digest != b.corpus_digest:
        cases, changed = _comparable_cases(first, second, pair_by_name)
        if not cases:
            raise CompareError(
                f"different corpora with no unchanged case in common: "
                f"{a.corpus_digest[:12]}… ({a.corpus_version}) and "
                f"{b.corpus_digest[:12]}… ({b.corpus_version})"
            )
    left, right = _episodes(first, cases), _episodes(second, cases)
    keys = sorted(left.keys() & right.keys())
    if not keys:
        raise CompareError("no episode is in both reports")

    vectors_a = [_vector(left[key][1]) for key in keys]
    vectors_b = [_vector(right[key][1]) for key in keys]
    everyone = list(range(len(keys)))
    pooled_a, pooled_b = _sum(vectors_a, everyone), _sum(vectors_b, everyone)
    rng = random.Random(SEED)
    samples: dict[str, list[float]] = {name: [] for name in HEADLINE}
    for _ in range(resamples):
        picks = rng.choices(everyone, k=len(keys))
        sample_a, sample_b = _sum(vectors_a, picks), _sum(vectors_b, picks)
        for name in HEADLINE:
            delta = _delta(RATES[name](sample_a), RATES[name](sample_b))
            if delta is not None:
                samples[name].append(delta)
    deltas = []
    for name in HEADLINE:
        value_a, value_b = RATES[name](pooled_a), RATES[name](pooled_b)
        drawn = samples[name]
        deltas.append(
            RateDelta(
                rate=name,
                a=value_a,
                b=value_b,
                delta=_delta(value_a, value_b),
                low=_percentile(drawn, 0.025) if drawn else None,
                high=_percentile(drawn, 0.975) if drawn else None,
            )
        )

    episodes = []
    transitions = {s: {t: 0 for t in STATUSES} for s in STATUSES}
    for key in keys:
        (status_a, counts_a), (status_b, counts_b) = left[key], right[key]
        transitions.setdefault(status_a, {}).setdefault(status_b, 0)
        transitions[status_a][status_b] += 1
        score_a, score_b = case_score(counts_a), case_score(counts_b)
        episodes.append(
            EpisodeDelta(
                case=key[0],
                source_id=key[1],
                status_a=status_a,
                status_b=status_b,
                score_a=score_a,
                score_b=score_b,
                delta=_delta(score_a, score_b),
            )
        )
    scored = [e for e in episodes if e.delta is not None]
    order = sorted(scored, key=lambda e: (e.delta or 0.0, e.case, e.source_id))
    return CompareReport(
        corpus_version=a.corpus_version,
        corpus_digest=a.corpus_digest,
        corpus_version_b=b.corpus_version,
        changed_cases=changed,
        paired_by_name=pair_by_name and a.corpus_digest != b.corpus_digest,
        row_a=first.label,
        row_b=second.label,
        split_a=a.split,
        split_b=b.split,
        paired=len(keys),
        only_a=[f"{c}/{s}" for c, s in sorted(left.keys() - right.keys())],
        only_b=[f"{c}/{s}" for c, s in sorted(right.keys() - left.keys())],
        resamples=resamples,
        rates=deltas,
        wins=sum(1 for e in scored if (e.delta or 0.0) > 1e-9),
        losses=sum(1 for e in scored if (e.delta or 0.0) < -1e-9),
        ties=sum(1 for e in scored if abs(e.delta or 0.0) <= 1e-9),
        unscored=len(episodes) - len(scored),
        transitions=transitions,
        drops=[e for e in order if (e.delta or 0.0) < -1e-9][:TOP],
        rises=[e for e in reversed(order) if (e.delta or 0.0) > 1e-9][:TOP],
    )


def _signed(value: float | None, digits: int = 2) -> str:
    return "—" if value is None else f"{value:+.{digits}f}"


def _episode_rows(items: list[EpisodeDelta]) -> list[list[str]]:
    return [
        [
            e.case,
            e.source_id,
            f"{e.status_a} → {e.status_b}",
            _fmt(e.score_a),
            _fmt(e.score_b),
            _signed(e.delta),
        ]
        for e in items
    ]


def to_markdown(report: CompareReport) -> str:
    """The human-readable comparison."""
    split = (
        report.split_a
        if report.split_a == report.split_b
        else f"{report.split_a} / {report.split_b}"
    )
    corpus = (
        f"Corpus **{report.corpus_version}** · digest `{report.corpus_digest[:12]}…`"
        if report.corpus_version == report.corpus_version_b and not report.changed_cases
        else f"Corpora **{report.corpus_version}** / **{report.corpus_version_b}**, "
        + (
            "cases paired by name (asserted unchanged)"
            if report.paired_by_name
            else "unchanged cases only"
        )
    )
    lines = [
        f"# {report.row_b} against {report.row_a}",
        "",
        f"{corpus} · split **{split}** · {report.paired} paired episode(s)",
        "",
        (
            f"Rates are pooled over the paired episodes; Δ is B − A, with a 95% paired "
            f"bootstrap interval over {report.resamples} resamples of episodes."
        ),
        "",
        "## Rates",
        "",
    ]
    lines += _table(
        ["Rate", "A", "B", "Δ", "95% interval"],
        [
            [
                r.rate,
                _fmt(r.a, DIGITS.get(r.rate, 2)),
                _fmt(r.b, DIGITS.get(r.rate, 2)),
                _signed(r.delta, DIGITS.get(r.rate, 2)),
                "—"
                if r.low is None
                else f"{_signed(r.low, DIGITS.get(r.rate, 2))} … "
                f"{_signed(r.high, DIGITS.get(r.rate, 2))}",
            ]
            for r in report.rates
        ],
    )
    lines += [
        "",
        "## Episodes",
        "",
        (
            f"Score (mean entity, edge and property F1): **{report.wins}** rose, "
            f"**{report.losses}** fell, {report.ties} unchanged, "
            f"{report.unscored} unscored."
        ),
        "",
    ]
    statuses = list(report.transitions)
    lines += _table(
        ["A \\ B", *statuses],
        [
            [s, *(str(report.transitions[s].get(t, 0)) for t in statuses)]
            for s in statuses
        ],
    )
    header = ["Case", "Episode", "Status", "A", "B", "Δ"]
    if report.drops:
        lines += ["", "### Largest drops", ""]
        lines += _table(header, _episode_rows(report.drops))
    if report.rises:
        lines += ["", "### Largest rises", ""]
        lines += _table(header, _episode_rows(report.rises))
    if report.changed_cases:
        lines += ["", "## Changed cases", ""]
        lines.append(f"Left out, content differs: {', '.join(report.changed_cases)}")
    if report.only_a or report.only_b:
        lines += ["", "## Unpaired", ""]
        if report.only_a:
            lines.append(f"Only in A: {', '.join(report.only_a)}")
        if report.only_b:
            lines.append(f"Only in B: {', '.join(report.only_b)}")
    return "\n".join(lines) + "\n"
