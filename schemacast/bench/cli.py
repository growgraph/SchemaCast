"""``schemacast-bench``: cast the corpus and score it, or compare two runs."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import click

from schemacast.bench.compare import CompareError, compare, load_report
from schemacast.bench.compare import to_markdown as compare_markdown
from schemacast.bench.corpus import CORPUS_VERSION, BenchCase, load_corpus
from schemacast.bench.report import to_markdown, write_report
from schemacast.bench.run import Row, load_prices, new_report, run_row
from schemacast.config import SchemaCastSettings
from schemacast.llm.cache import CacheMissError
from schemacast.llm.client import LLMConfigurationError


def select_cases(
    corpus: list[BenchCase],
    split: str,
    families: tuple[str, ...],
    cases: tuple[str, ...],
) -> list[BenchCase]:
    """Narrow *corpus* by split, family and case name; filters compose."""
    selected = corpus if split == "all" else [c for c in corpus if c.split == split]
    if families:
        selected = [c for c in selected if c.family in families]
    if cases:
        selected = [c for c in selected if c.name in cases]
    return selected


class _RunByDefault(click.Group):
    """A group that runs ``run`` when the first argument names no subcommand."""

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        if not args or (
            args[0] not in self.commands and args[0] not in ("--help", "-h")
        ):
            args = ["run", *args]
        return super().parse_args(ctx, args)


@click.group(cls=_RunByDefault)
def main() -> None:
    """Benchmark text-to-graph casting against the bundled corpus.

    Without a subcommand, `run` is meant: `schemacast-bench --model NAME`.
    """


@main.command()
@click.option(
    "--model",
    "models",
    multiple=True,
    metavar="NAME",
    help="Model to benchmark, repeatable; the endpoint is SCHEMACAST_LLM_BASE_URL. "
    "Without it only the free oracle row runs.",
)
@click.option(
    "--oracle/--no-oracle",
    default=True,
    show_default=True,
    help="Add the oracle row: the expected graph through the whole pipeline. Free.",
)
@click.option(
    "--split",
    type=click.Choice(["dev", "test", "all"]),
    default="all",
    show_default=True,
    help="dev is tuned against; only test is publishable.",
)
@click.option("--family", "families", multiple=True, help="Only these families.")
@click.option(
    "--case", "cases", multiple=True, help="Only these cases, e.g. units/lab."
)
@click.option(
    "--replay",
    is_flag=True,
    help="Serve every reply from the cache; a miss stops the run.",
)
@click.option(
    "--out",
    "out_dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=Path("bench-results"),
    show_default=True,
    help="Where the JSON and Markdown reports go.",
)
@click.option("--concurrency", type=int, default=8, show_default=True)
def run(
    models: tuple[str, ...],
    oracle: bool,
    split: str,
    families: tuple[str, ...],
    cases: tuple[str, ...],
    replay: bool,
    out_dir: Path,
    concurrency: int,
) -> None:
    """Cast and score the corpus; write JSON and Markdown reports."""
    settings = SchemaCastSettings()
    if replay:
        settings.cache = settings.cache.model_copy(update={"require_hit": True})
    names = list(models)
    if not names and not oracle:
        raise click.UsageError("nothing to run: give --model or keep --oracle")
    selected = select_cases(load_corpus(), split, families, cases)
    if not selected:
        raise click.UsageError("no case matches the filters")
    prices, prices_source = load_prices()
    click.echo(
        f"Corpus {CORPUS_VERSION} [{split}]: {len(selected)} case(s); "
        f"rows: {', '.join((['oracle'] if oracle else []) + names)}",
        err=True,
    )
    started = datetime.now(UTC)
    rows: list[Row] = []
    try:
        if oracle:
            rows.append(
                asyncio.run(
                    run_row(selected, settings, oracle=True, concurrency=concurrency)
                )
            )
        for name in names:
            row_settings = settings.model_copy(
                update={"llm": settings.llm.model_copy(update={"model": name})}
            )
            rows.append(
                asyncio.run(
                    run_row(
                        selected, row_settings, concurrency=concurrency, prices=prices
                    )
                )
            )
    except (LLMConfigurationError, CacheMissError) as error:
        raise click.ClickException(str(error)) from error
    report = new_report(
        split, started.isoformat(timespec="seconds"), rows, prices_source
    )
    click.echo(to_markdown(report))
    json_path, md_path = write_report(
        report, out_dir, started.strftime("%Y%m%dT%H%M%SZ")
    )
    click.echo(f"Wrote {json_path} and {md_path}", err=True)


@main.command("compare")
@click.argument(
    "report_a", type=click.Path(exists=True, dir_okay=False, path_type=Path)
)
@click.argument(
    "report_b", type=click.Path(exists=True, dir_okay=False, path_type=Path)
)
@click.option(
    "--row-a", help="Row of REPORT_A; default: its only row, or its only model row."
)
@click.option("--row-b", help="Row of REPORT_B; default as for --row-a.")
@click.option(
    "--pair-by-name",
    is_flag=True,
    help="Across corpus versions, pair cases a report has no content digest for "
    "by name: you assert they did not change.",
)
@click.option(
    "--json",
    "json_path",
    type=click.Path(dir_okay=False, path_type=Path),
    help="Also write the comparison as JSON here.",
)
def compare_command(
    report_a: Path,
    report_b: Path,
    row_a: str | None,
    row_b: str | None,
    pair_by_name: bool,
    json_path: Path | None,
) -> None:
    """Read REPORT_B against REPORT_A, episode by episode, on the same corpus."""
    try:
        result = compare(
            load_report(report_a),
            load_report(report_b),
            row_a=row_a,
            row_b=row_b,
            pair_by_name=pair_by_name,
        )
    except CompareError as error:
        raise click.ClickException(str(error)) from error
    click.echo(compare_markdown(result), nl=False)
    if json_path is not None:
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")
