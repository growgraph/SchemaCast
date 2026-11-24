# Hello: companies and deals

Five one-line news notes about made-up companies, turned into a graph of who runs what and who
bought whom. The model's replies were recorded once and ship in `cache/`, so this needs no key:

```bash
uv run python examples/hello/demo.py
```

It prints every fact it found with the words it came from, and writes the graph to `out/hello/`.

| File | Holds |
|---|---|
| `schema.yaml` | what to look for: companies, people, `CEO_OF`, `ACQUIRED` with a price |
| `episodes.jsonl` | the five notes |
| `demo.py` | the whole program, about ten lines |
| `cache/` | the recorded replies |
| `expected.txt` | what the demo prints, shown in the docs |

The same run from the command line:

```bash
SCHEMACAST_CACHE_DIR=examples/hello/cache uv run schemacast cast --replay \
    --schema examples/hello/schema.yaml --episodes examples/hello/episodes.jsonl --out out/hello
```

Drop `--replay` and the cache setting, set `SCHEMACAST_LLM_API_KEY`, and it runs on a live model;
change the notes or the schema to try your own.

A change to the schema, the notes or SchemaCast's prompt misses the recorded replies. Record them
again (one paid call per note) with `SCHEMACAST_LLM_API_KEY` set and no other `SCHEMACAST_`
settings:

```bash
rm -rf examples/hello/cache && uv run python examples/hello/demo.py --record > examples/hello/expected.txt
```
