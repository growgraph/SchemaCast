"""Command line: ``schemacast``."""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING

import click
from graflo.architecture.schema.context import instance_json_schema, render_type_sheet

from schemacast import __version__
from schemacast.config import SchemaCastSettings
from schemacast.episode import Episode, read_episodes
from schemacast.llm.cache import CacheMissError
from schemacast.llm.client import LLMConfigurationError
from schemacast.schema_io import load_contract, load_schema
from schemacast.select import SelectionConfig, SelectionError, select

if TYPE_CHECKING:
    from schemacast.cast import CastResult
    from schemacast.pick import PickItem

#: ``cast`` exit status when some episodes failed at the provider (sysexits
#: ``EX_TEMPFAIL``): the output is incomplete, and a rerun retries them.
EX_TEMPFAIL = 75


def cast_summary(
    result: CastResult, out_dir: Path, *, cache_enabled: bool
) -> tuple[list[str], int]:
    """The lines ``cast`` prints and its exit status.

    A ``provider_error`` is never cached, so a rerun retries it; every other
    failure is replayed from the cache, so a rerun reproduces it.
    """
    manifest = result.manifest
    kinds = Counter(e.failure for e in result.episodes if e.status == "failed")
    failed = f"{manifest.status['failed']} failed"
    if kinds:
        failed += ": " + ", ".join(f"{k} {n}" for k, n in sorted(kinds.items()))
    summary = (
        f"{manifest.episodes} episodes ({manifest.status['ok']} ok, "
        f"{manifest.status['partial']} partial, {failed}): "
        f"{manifest.mentions} mentions -> {manifest.vertices} vertices, "
        f"{manifest.edges} edges, {manifest.staged} staged; "
        f"{manifest.quarantined} quarantined, {manifest.unmapped} unmapped -> {out_dir}"
    )
    lines = [summary]
    retry = kinds.get("provider_error", 0)
    if retry:
        rest = (
            "cached replies cost nothing"
            if cache_enabled
            else "the cache is off, so every episode is cast again"
        )
        lines.append(
            f"{retry} episode(s) failed at the provider: rerun the same command to "
            f"retry them; {rest}."
        )
    if manifest.episodes and manifest.status["failed"] == manifest.episodes:
        return lines, 1
    return lines, EX_TEMPFAIL if retry else 0


@click.group()
@click.version_option(__version__, prog_name="schemacast")
def main() -> None:
    """Cast text into a labeled property graph against a governed GraFlo schema."""


@main.command()
@click.option(
    "--schema",
    "schema_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="GraFlo schema or manifest (YAML or JSON).",
)
@click.option(
    "--json-schema",
    "as_json_schema",
    is_flag=True,
    help="Print the JSON Schema of the reply instead of the type sheet.",
)
def sheet(schema_path: Path, as_json_schema: bool) -> None:
    """Print what a model is shown for SCHEMA. Makes no model call."""
    schema = load_schema(schema_path)
    if as_json_schema:
        click.echo(json.dumps(instance_json_schema(schema), indent=2))
    else:
        click.echo(render_type_sheet(schema), nl=False)


@main.command("select")
@click.option(
    "--schema",
    "schema_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="GraFlo schema or manifest (YAML or JSON).",
)
@click.option("--text", help="A single text to select for.")
@click.option(
    "--episodes",
    "episodes_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Episodes, one JSON object per line.",
)
@click.option(
    "--sheet", "show_sheet", is_flag=True, help="Print each slice's type sheet."
)
def select_command(
    schema_path: Path, text: str | None, episodes_path: Path | None, show_sheet: bool
) -> None:
    """Show the slice of SCHEMA each text would be cast against. Makes no model call.

    Prints one JSON line per text: the mode, the seeds and how each lane
    ranked them, the retrieval metrics and the slice's vertex and edge counts.
    """
    if (text is None) == (episodes_path is None):
        raise click.UsageError("Give exactly one of --text or --episodes.")
    settings = SchemaCastSettings()
    config = SelectionConfig(
        max_tokens=settings.slice_max_tokens, max_elements=settings.slice_max_elements
    )
    schema = load_schema(schema_path)
    episodes: Iterable[Episode]
    if text is not None:
        episodes = [Episode(text=text, source_id="text")]
    else:
        assert episodes_path is not None
        episodes = read_episodes(episodes_path)
    failed = False
    for episode in episodes:
        try:
            selection = select(schema, episode.text, config)
        except SelectionError as error:
            failed = True
            click.echo(
                json.dumps(
                    {
                        "source_id": episode.source_id,
                        "error": error.reason,
                        "message": str(error),
                    }
                )
            )
            continue
        if show_sheet:
            click.echo(f"# {episode.source_id} ({selection.mode})")
            click.echo(render_type_sheet(selection.slice), nl=False)
            continue
        core = selection.slice.core_schema
        record = {
            "source_id": episode.source_id,
            "mode": selection.mode,
            "seeds": [
                {
                    "seed": hit.vertex or list(hit.edge_id or ()),
                    "score": round(hit.score, 4),
                    "ranks": hit.ranks,
                }
                for hit in selection.seeds
            ],
            "metrics": selection.metrics.model_dump(),
            "vertices": len(core.vertex_config.vertices),
            "edges": len(list(core.edge_config.values())),
        }
        click.echo(json.dumps(record, ensure_ascii=False))
    if failed:
        raise SystemExit(1)


@main.command()
@click.option(
    "--schema",
    "schema_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="GraFlo schema or manifest (YAML or JSON).",
)
@click.option(
    "--episodes",
    "episodes_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Episodes, one JSON object per line.",
)
@click.option(
    "--out",
    "out_dir",
    required=True,
    type=click.Path(file_okay=False, path_type=Path),
    help="Directory for the graph, groundings, quarantine and manifest.",
)
@click.option(
    "--db",
    "db_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="graflo connection file (YAML); the graph is written there too.",
)
@click.option(
    "--replay",
    is_flag=True,
    help="Serve every reply from the cache; a miss stops the run. Makes no request.",
)
@click.option(
    "--recreate-schema",
    is_flag=True,
    help="Drop and redefine the schema on the --db target first.",
)
@click.option(
    "--concurrency", default=8, show_default=True, help="Episodes cast at once."
)
def cast(
    schema_path: Path,
    episodes_path: Path,
    out_dir: Path,
    db_path: Path | None,
    replay: bool,
    recreate_schema: bool,
    concurrency: int,
) -> None:
    """Cast EPISODES into SCHEMA and save the result under OUT.

    Each text becomes a mention graph; what its keys identify is the graph, the
    rest is staged. With --db, keys are looked up there too; a manifest's
    connector-owned types are linked, never created.

    Episodes that produce nothing are quarantined and the run goes on; a
    misconfigured or unreachable model, or a cache miss under --replay, stops it.

    Exit status: 0 when every episode was cast or failed on its own content
    (too long, refused, truncated, unparseable), which a rerun reproduces; 75
    when some failed at the provider, which a rerun retries; 1 when the run
    stopped or every episode failed.
    """
    from schemacast.cast import SchemaCaster
    from schemacast.output import QUARANTINE_FILE, save_cast
    from schemacast.sink import JsonlQuarantineSink

    settings = SchemaCastSettings()
    if replay:
        settings.cache = settings.cache.model_copy(update={"require_hit": True})
    out_dir.mkdir(parents=True, exist_ok=True)
    quarantine = out_dir / QUARANTINE_FILE
    quarantine.unlink(missing_ok=True)
    schema, owned = load_contract(schema_path)
    caster = SchemaCaster(
        schema,
        settings=settings,
        sink=JsonlQuarantineSink(quarantine),
        max_concurrent_episodes=concurrency,
        owned=owned,
    )
    try:
        result = asyncio.run(caster.cast(read_episodes(episodes_path)))
    except (LLMConfigurationError, CacheMissError) as error:
        raise click.ClickException(str(error)) from error

    report = written = None
    if db_path is not None:
        from schemacast.write import load_connection, write_cast

        written, report = asyncio.run(
            write_cast(
                result,
                load_connection(db_path),
                owned=owned,
                recreate_schema=recreate_schema,
            )
        )
    save_cast(result, out_dir, write_report=report, resolution=written)
    lines, status = cast_summary(result, out_dir, cache_enabled=settings.cache.enabled)
    for line in lines:
        click.echo(line, err=True)
    if status:
        raise SystemExit(status)


def read_pick_items(path: Path) -> list[PickItem]:
    """Pick input: one JSON object per line, each a ``PickItem``.

    Raises:
        click.ClickException: a line that is not a pick item.
    """
    from pydantic import ValidationError

    from schemacast.pick import PickItem

    items: list[PickItem] = []
    with open(path, encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                items.append(PickItem.model_validate_json(line))
            except ValidationError as error:
                raise click.ClickException(f"{path}:{number}: {error}") from error
    return items


@main.command()
@click.option(
    "--input",
    "input_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="One JSON object per line: an episode, its staged mentions with their "
    "candidates, and the entities its metadata names.",
)
@click.option(
    "--out",
    "out_dir",
    required=True,
    type=click.Path(file_okay=False, path_type=Path),
    help="Directory for picks.jsonl, episodes.jsonl and manifest.json.",
)
@click.option(
    "--replay",
    is_flag=True,
    help="Serve every reply from the cache; a miss stops the run. Makes no request.",
)
@click.option(
    "--concurrency", default=8, show_default=True, help="Episodes picked at once."
)
def pick(input_path: Path, out_dir: Path, replay: bool, concurrency: int) -> None:
    """Link staged mentions to their candidates: one model call per episode.

    Each staged mention gets one of its candidates or none, checked without a
    model. A pick links; it writes nothing.

    Exit status: 0 when every call answered; 75 when some failed at the
    provider, which a rerun retries; 1 when the run stopped or every call failed.
    """
    from schemacast.output import save_picks
    from schemacast.pick import Picker

    settings = SchemaCastSettings()
    if replay:
        settings.cache = settings.cache.model_copy(update={"require_hit": True})
    items = read_pick_items(input_path)
    picker = Picker(settings=settings, max_concurrent_episodes=concurrency)
    try:
        result = asyncio.run(picker.pick(items))
    except (LLMConfigurationError, CacheMissError) as error:
        raise click.ClickException(str(error)) from error
    save_picks(result, out_dir)
    manifest = result.manifest
    click.echo(
        f"{manifest.episodes} episodes, {manifest.mentions} staged mentions: "
        f"{manifest.picked} picked, {manifest.none} none, {manifest.rejected} "
        f"rejected; {manifest.failed} episode(s) failed -> {out_dir}",
        err=True,
    )
    asked = [e for e in result.episodes if e.status != "skipped"]
    if asked and all(e.status == "failed" for e in asked):
        raise SystemExit(1)
    if any(e.failure == "provider_error" for e in result.episodes):
        raise SystemExit(EX_TEMPFAIL)


@main.command()
@click.option(
    "--host", help="Address to bind; default SCHEMACAST_SERVE_HOST or 127.0.0.1."
)
@click.option("--port", type=int, help="Port; default SCHEMACAST_SERVE_PORT or 8997.")
def serve(host: str | None, port: int | None) -> None:
    """Serve POST /cast and POST /pick over HTTP (needs the `server` extra).

    Every request needs `Authorization: Bearer $SCHEMACAST_SERVE_API_KEY`; without
    a key the service refuses to cast unless SCHEMACAST_SERVE_ALLOW_ANONYMOUS is
    set. Model settings come from SCHEMACAST_LLM_*.
    """
    try:
        from schemacast.serve import run
    except ImportError as error:
        raise click.ClickException(
            f"{error.name or 'fastapi'} is missing: pip install 'schemacast[server]'"
        ) from error
    from schemacast.config import ServeSettings

    settings = ServeSettings()
    overrides = {k: v for k, v in (("host", host), ("port", port)) if v is not None}
    run(settings.model_copy(update=overrides))
