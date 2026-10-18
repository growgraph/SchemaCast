"""Rates from pooled counts, and the Markdown and JSON reports."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from schemacast.bench.run import BenchReport, CaseRecord, Row
from schemacast.bench.score import Counts, total

Rate = Callable[[Counts], float | None]


def ratio(num: int, den: int) -> float | None:
    """*num* / *den*, or None when nothing was counted."""
    return num / den if den else None


def f1(precision: float | None, recall: float | None) -> float | None:
    """Harmonic mean; None when either side is undefined."""
    if precision is None or recall is None:
        return None
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def _complement(value: float | None) -> float | None:
    return None if value is None else 1.0 - value


def _entity_f1(c: Counts) -> float | None:
    return f1(
        ratio(c.entities_matched, c.entities_produced),
        ratio(c.entities_matched, c.entities_expected),
    )


def _edge_f1(c: Counts) -> float | None:
    return f1(
        ratio(c.edges_matched, c.edges_produced),
        ratio(c.edges_matched, c.edges_expected),
    )


def _prop_f1(c: Counts) -> float | None:
    return f1(
        ratio(c.props_matched, c.props_produced),
        ratio(c.props_matched, c.props_expected),
    )


#: Column → rate. Hallucination and conformance read the first reply, before
#: any repair, except where a column says *final*.
RATES: dict[str, Rate] = {
    "conformance": lambda c: _complement(ratio(c.nonconforming_first, c.emitted_first)),
    "conformance_final": lambda c: _complement(
        ratio(c.nonconforming_final, c.emitted_final)
    ),
    "type_hallucination": lambda c: ratio(c.unknown_type_first, c.vertices_first),
    "relation_hallucination": lambda c: ratio(c.undeclared_edge_first, c.edges_first),
    "property_hallucination": lambda c: ratio(
        c.undeclared_property_first, c.properties_first
    ),
    "ungrounded": lambda c: ratio(c.ungrounded_first, c.emitted_first),
    "unfaithful_value": lambda c: ratio(c.unfaithful_value_first, c.properties_first),
    "entity_precision": lambda c: ratio(c.entities_matched, c.entities_produced),
    "entity_recall": lambda c: ratio(c.entities_matched, c.entities_expected),
    "entity_f1": _entity_f1,
    "edge_precision": lambda c: ratio(c.edges_matched, c.edges_produced),
    "edge_recall": lambda c: ratio(c.edges_matched, c.edges_expected),
    "edge_f1": _edge_f1,
    "property_precision": lambda c: ratio(c.props_matched, c.props_produced),
    "property_recall": lambda c: ratio(c.props_matched, c.props_expected),
    "property_f1": _prop_f1,
    "direction": lambda c: ratio(c.edges_matched, c.edges_matched + c.edges_reversed),
    "span_accuracy": lambda c: ratio(c.spans_hit, c.spans_checked),
    "mention_agreement": lambda c: ratio(c.mention_agree, c.entities_matched),
    "forbidden_written": lambda c: ratio(c.forbidden_written, c.forbidden_expected),
    "seed_recall": lambda c: ratio(c.gold_types_seeded, c.gold_types),
    "closure_recall": lambda c: ratio(c.gold_types_in_slice, c.gold_types),
    "closure_edge_recall": lambda c: ratio(c.gold_edges_in_slice, c.gold_edges),
    "recast_added": lambda c: ratio(c.recast_added, c.recast_vertices),
    "recast_duplicated": lambda c: ratio(c.recast_duplicated, c.recast_minted),
    "tokens_per_episode": lambda c: ratio(c.input_tokens + c.output_tokens, c.episodes),
    "requests_per_episode": lambda c: ratio(c.requests, c.episodes),
}


def rates(counts: Counts) -> dict[str, float | None]:
    """Every rate of :data:`RATES` for *counts*."""
    return {name: rate(counts) for name, rate in RATES.items()}


def case_score(counts: Counts) -> float | None:
    """Mean of the entity, edge and property F1 a case defines."""
    parts = [
        v
        for v in (_entity_f1(counts), _edge_f1(counts), _prop_f1(counts))
        if v is not None
    ]
    return sum(parts) / len(parts) if parts else None


def _fmt(value: float | None, digits: int = 2) -> str:
    return "—" if value is None else f"{value:.{digits}f}"


def _table(header: list[str], rows: list[list[str]]) -> list[str]:
    return [
        "| " + " | ".join(header) + " |",
        "|" + "|".join("---" for _ in header) + "|",
        *("| " + " | ".join(row) + " |" for row in rows),
    ]


def _families(row: Row) -> dict[str, list[CaseRecord]]:
    grouped: dict[str, list[CaseRecord]] = {}
    for case in row.cases:
        grouped.setdefault(case.family, []).append(case)
    return dict(sorted(grouped.items()))


def to_markdown(report: BenchReport) -> str:
    """The human-readable report."""
    status = (
        "publishable"
        if report.publishable
        else "**not publishable**: "
        + (
            "the cases differ from the pinned corpus"
            if report.corpus_modified
            else "only `--split test` is held out"
        )
    )
    lines = [
        "# SchemaCast benchmark",
        "",
        (
            f"Corpus **{report.corpus_version}** · digest `{report.corpus_digest[:12]}…` · "
            f"split **{report.split}** ({status}) · schemacast {report.schemacast_version} · "
            f"graflo {report.graflo_version} · started {report.started}"
        ),
        "",
        "Conformance and hallucination read the first reply, before any repair.",
        "",
        "## Summary",
        "",
    ]
    summary = []
    for row in report.rows:
        r = rates(row.counts)
        c = row.counts
        summary.append(
            [
                row.label,
                str(c.episodes),
                str(c.failed),
                _fmt(r["conformance"]),
                _fmt(r["conformance_final"]),
                _fmt(r["type_hallucination"]),
                _fmt(r["relation_hallucination"]),
                _fmt(r["property_hallucination"]),
                _fmt(r["ungrounded"]),
                _fmt(r["unfaithful_value"]),
                _fmt(r["entity_f1"]),
                _fmt(r["edge_f1"]),
                _fmt(r["property_precision"]),
                _fmt(r["property_recall"]),
                _fmt(r["direction"]),
                _fmt(r["span_accuracy"]),
                f"{c.forbidden_written}/{c.forbidden_expected}",
                _fmt(r["tokens_per_episode"], 0),
                _fmt(r["requests_per_episode"]),
                "n/a" if row.usd is None else f"{row.usd:.4f}",
            ]
        )
    lines += _table(
        [
            "Model",
            "Episodes",
            "Failed",
            "Conformance",
            "Final",
            "Type H",
            "Relation H",
            "Property H",
            "Ungrounded",
            "Unfaithful",
            "Entity F1",
            "Edge F1",
            "Prop P",
            "Prop R",
            "Direction",
            "Span",
            "Forbidden",
            "Tokens/ep",
            "Requests/ep",
            "USD",
        ],
        summary,
    )
    lines += ["", "## Per family", ""]
    family_rows = []
    for row in report.rows:
        for family, cases in _families(row).items():
            scores = [
                s for s in (case_score(case.counts) for case in cases) if s is not None
            ]
            counts = total([case.counts for case in cases])
            r = rates(counts)
            spread = f"{min(scores):.2f}–{max(scores):.2f}" if scores else "—"
            family_rows.append(
                [
                    row.label,
                    family,
                    str(len(cases)),
                    _fmt(sum(scores) / len(scores) if scores else None),
                    spread,
                    _fmt(r["entity_f1"]),
                    _fmt(r["edge_f1"]),
                    _fmt(r["property_f1"]),
                    _fmt(r["direction"]),
                    _fmt(r["mention_agreement"]),
                    f"{counts.forbidden_written}/{counts.forbidden_expected}",
                ]
            )
    lines += _table(
        [
            "Model",
            "Family",
            "Cases",
            "Score",
            "Range",
            "Entity F1",
            "Edge F1",
            "Prop F1",
            "Direction",
            "Mention",
            "Forbidden",
        ],
        family_rows,
    )
    lines += [
        "",
        (
            "Score is the mean over cases of the mean entity, edge and property F1; "
            "range is its lowest and highest case."
        ),
        "",
        "## Selection",
        "",
        "Episodes whose schema was sliced; a schema under budget passes whole.",
        "",
    ]
    lines += _table(
        ["Model", "Sliced", "Seed recall", "Closure recall", "Closure edge recall"],
        [
            [
                row.label,
                str(row.counts.sliced),
                _fmt(rates(row.counts)["seed_recall"]),
                _fmt(rates(row.counts)["closure_recall"]),
                _fmt(rates(row.counts)["closure_edge_recall"]),
            ]
            for row in report.rows
        ],
    )
    lines += [
        "",
        "## Re-cast",
        "",
        (
            "A second cast of each re-cast episode with a fresh model call. *Added*: "
            "natural or hash identities the first cast lacked. *Duplicated*: assigned "
            "or blank vertices a store would hold twice."
        ),
        "",
    ]
    lines += _table(
        ["Model", "Vertices", "Added", "Duplicated"],
        [
            [
                row.label,
                str(row.counts.recast_vertices),
                f"{row.counts.recast_added} ({_fmt(rates(row.counts)['recast_added'])})",
                (
                    f"{row.counts.recast_duplicated} "
                    f"({_fmt(rates(row.counts)['recast_duplicated'])})"
                ),
            ]
            for row in report.rows
        ],
    )
    if report.prices_source:
        lines += ["", f"Prices: {report.prices_source}"]
    return "\n".join(lines) + "\n"


def write_report(report: BenchReport, out_dir: Path, stamp: str) -> tuple[Path, Path]:
    """Write ``<stamp>.json`` (every episode's counts and rates) and ``<stamp>.md``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    data = report.model_dump(mode="json")
    for row, raw in zip(report.rows, data["rows"], strict=True):
        raw["rates"] = rates(row.counts)
        for case, case_raw in zip(row.cases, raw["cases"], strict=True):
            case_raw["rates"] = rates(case.counts)
            case_raw["score"] = case_score(case.counts)
    json_path = out_dir / f"{stamp}.json"
    md_path = out_dir / f"{stamp}.md"

    json_path.write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    md_path.write_text(to_markdown(report), encoding="utf-8")
    return json_path, md_path
