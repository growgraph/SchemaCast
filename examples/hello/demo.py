"""Cast five news notes about companies and deals; print every fact and its quote.

Replays the replies recorded in cache/, so it needs no key and makes no request.
--record calls the model instead (set SCHEMACAST_LLM_API_KEY).
"""

import asyncio
from pathlib import Path

import click

from schemacast import SchemaCaster, load_schema, read_episodes, save_bundle, save_cast
from schemacast.config import CacheSettings, SchemaCastSettings

HERE = Path(__file__).parent


@click.command()
@click.option("--record", is_flag=True, help="Call the model and record its replies.")
def main(record: bool) -> None:
    cache = CacheSettings(dir=HERE / "cache", require_hit=not record)
    settings = SchemaCastSettings(cache=cache)
    caster = SchemaCaster(load_schema(HERE / "schema.yaml"), settings=settings)
    result = asyncio.run(caster.cast(read_episodes(HERE / "episodes.jsonl")))
    for g in result.groundings:
        what = g.property or (g.edge[2] if g.edge else g.vertex)
        note = "denied" if g.negated else "" if g.modality == "actual" else g.modality
        when = f"{g.when.start}..{g.when.end or ''}" if g.when else ""
        click.echo(f"{g.source_id}  {what:<9} {g.quote!r}  {note} {when}".rstrip())
    save_cast(result, "out/hello")
    save_bundle(result, "out/hello/bundle")


if __name__ == "__main__":
    main()
