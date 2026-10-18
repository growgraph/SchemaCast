"""Command line: ``schemacast``."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterable
from pathlib import Path

import click
from graflo.architecture.schema.context import instance_json_schema, render_type_sheet

from schemacast import __version__
from schemacast.config import SchemaCastSettings
from schemacast.episode import Episode, read_candidates, read_episodes, with_known
from schemacast.llm.cache import CacheMissError
from schemacast.llm.client import LLMConfigurationError
from schemacast.schema_io import load_schema
from schemacast.select import SelectionConfig, SelectionError, select


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
@click.option(
    "--known",
    "known_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Known entities, one candidate per line, for episodes that carry none.",
)
def cast(
    schema_path: Path,
    episodes_path: Path,
    out_dir: Path,
    db_path: Path | None,
    replay: bool,
    recreate_schema: bool,
    concurrency: int,
    known_path: Path | None,
) -> None:
    """Cast EPISODES into SCHEMA and save the result under OUT.

    Episodes that produce nothing are quarantined and the run goes on; a
    misconfigured model, or a cache miss under --replay, stops it.
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
    caster = SchemaCaster(
        load_schema(schema_path),
        settings=settings,
        sink=JsonlQuarantineSink(quarantine),
        max_concurrent_episodes=concurrency,
    )
    known = read_candidates(known_path) if known_path is not None else []
    try:
        result = asyncio.run(
            caster.cast(with_known(read_episodes(episodes_path), known))
        )
    except (LLMConfigurationError, CacheMissError) as error:
        raise click.ClickException(str(error)) from error

    report = None
    if db_path is not None:
        from schemacast.write import load_connection, write_cast

        report = asyncio.run(
            write_cast(
                result, load_connection(db_path), recreate_schema=recreate_schema
            )
        )
    save_cast(result, out_dir, write_report=report)
    manifest = result.manifest
    click.echo(
        f"{manifest.episodes} episodes ({manifest.status['ok']} ok, "
        f"{manifest.status['partial']} partial, {manifest.status['failed']} failed): "
        f"{manifest.vertices} vertices, {manifest.edges} edges, "
        f"{manifest.quarantined} quarantined, {manifest.unmapped} unmapped -> {out_dir}",
        err=True,
    )


@main.command()
@click.option(
    "--host", help="Address to bind; default SCHEMACAST_SERVE_HOST or 127.0.0.1."
)
@click.option("--port", type=int, help="Port; default SCHEMACAST_SERVE_PORT or 8997.")
def serve(host: str | None, port: int | None) -> None:
    """Serve POST /cast over HTTP (needs the `server` extra).

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
