# Benchmark corpus

The cases and schemas `schemacast-bench` scores against. How the benchmark works, what it
measures and how to run it: the [benchmark guide](https://growgraph.github.io/SchemaCast/guides/benchmark/)
(`docs/guides/benchmark.md` in this repository).

## Adding a case

Write `case.yaml`, `episodes.jsonl` and `expected.yaml` under `cases/<family>/<instance>/`, add
the case to `cases/SPLITS.yaml`, run `uv run pytest test/test_bench.py`, and set
`CORPUS_DIGEST` in `corpus.py` to the value the failing digest test prints, with a new
`CORPUS_VERSION`.

`expected.yaml` may also hold, per episode, `forbidden` (items a cast must not write) and
`allowed` (items it may write without being required to).

A corpus of your own uses the same layout under `DIR/cases/` and `DIR/schemas/`:
`schemacast-bench --corpus DIR`, or `schemacast-bench score --corpus DIR CASTS…` for saved casts.
