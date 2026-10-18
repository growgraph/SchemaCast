# Benchmark corpus

The cases and schemas `schemacast-bench` scores against. How the benchmark works, what it
measures and how to run it: the [benchmark guide](https://growgraph.github.io/SchemaCast/guides/benchmark/)
(`docs/guides/benchmark.md` in this repository).

## Adding a case

Write `case.yaml`, `episodes.jsonl` and `expected.yaml` under `cases/<family>/<instance>/`, add
the case to `cases/SPLITS.yaml`, run `uv run pytest test/test_bench.py`, and set
`CORPUS_DIGEST` in `corpus.py` to the value the failing digest test prints, with a new
`CORPUS_VERSION`.
