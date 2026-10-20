# SchemaCast <img src="https://raw.githubusercontent.com/growgraph/SchemaCast/refs/heads/main/docs/assets/logo.png" alt="SchemaCast logo" height="32" width="32"/>

**Short texts in, a labeled property graph out, typed by a GraFlo schema you govern.**

[![tests](https://img.shields.io/github/actions/workflow/status/growgraph/SchemaCast/tests.yml?branch=main&label=tests)](https://github.com/growgraph/SchemaCast/actions/workflows/tests.yml)
[![pre-commit](https://img.shields.io/github/actions/workflow/status/growgraph/SchemaCast/pre-commit.yml?branch=main&label=pre-commit)](https://github.com/growgraph/SchemaCast/actions/workflows/pre-commit.yml)
[![PyPI](https://img.shields.io/pypi/v/schemacast?color=224777)](https://pypi.org/project/schemacast/)
[![Python](https://img.shields.io/pypi/pyversions/schemacast?color=224777)](https://pypi.org/project/schemacast/)
[![Docs](https://img.shields.io/badge/docs-growgraph.github.io-224777)](https://growgraph.github.io/SchemaCast/)
[![License](https://img.shields.io/badge/license-Apache--2.0-224777)](https://github.com/growgraph/SchemaCast/blob/main/LICENSE)

SchemaCast reads messages, tickets and notes, and writes what they state as vertices and edges of a
[GraFlo](https://github.com/growgraph/graflo) schema. Each text gets one model call, checked
against the schema; what fails is corrected at most twice, then quarantined. Every vertex, edge
and property keeps the quote it was read from.

OntoCast builds and evolves an ontology from documents and writes RDF; SchemaCast casts short,
frequent texts into a property-graph schema you already govern.

**Status: alpha.** The API may change between minor releases.

## Quick start

```bash
uv add schemacast        # or: pip install schemacast
```

Point it at any OpenAI-compatible endpoint:

```bash
# OpenAI (the default model is gpt-5.6-luna)
export SCHEMACAST_LLM_API_KEY=sk-...

# or Ollama
export SCHEMACAST_LLM_BASE_URL=http://localhost:11434/v1
export SCHEMACAST_LLM_MODEL=qwen3:8b
export SCHEMACAST_LLM_API_KEY=local
```

Then, from a clone of the repository, with the example in [`examples/tickets/`](https://github.com/growgraph/SchemaCast/tree/main/examples/tickets):

```bash
schemacast sheet  --schema examples/tickets/schema.yaml        # what the model is shown; no call
schemacast select --schema examples/tickets/schema.yaml --episodes examples/tickets/episodes.jsonl
schemacast cast   --schema examples/tickets/schema.yaml --episodes examples/tickets/episodes.jsonl \
               --out out/tickets [--db examples/connections/neo4j.yaml] [--replay]
```

An episodes file holds one JSON object per line:

```json
{"source_id": "mail-1041", "text": "Customer ACC-2291 opened ticket HD-4411 ...", "reference_time": "2026-03-02T09:14:00Z"}
```

From Python:

```python
import asyncio
from schemacast import SchemaCaster, load_schema, read_episodes

schema = load_schema("examples/tickets/schema.yaml")
result = asyncio.run(
    SchemaCaster(schema).cast(read_episodes("examples/tickets/episodes.jsonl"))
)
```

`--out` gets each text's mention graph, what their keys identify, what is staged until a lookup
can identify it, a grounding per assertion, the quarantine, one result per text and a run
manifest. `--replay` serves every reply from the cache and makes no
request; everything but the manifest is byte-identical. Writing upserts by identity, so writing a
cast twice changes nothing in a database that upserts.

## Benchmark

`schemacast-bench` casts a bundled corpus of texts whose graphs are known and scores the result
exactly, with no model as judge: conformance, hallucination, precision and recall, direction,
spans, mention versus mint, injection, selection and re-cast stability, with tokens and cost.

## Not yet

**Registry mode** is planned: selection across the schemas of a GraFlo schema registry, and an
HTTP service. Today SchemaCast works on one schema file.

## Documentation

[growgraph.github.io/SchemaCast](https://growgraph.github.io/SchemaCast/): installation, how a cast
works, identities and mentions, preparing a schema, replay, writing to a database, the benchmark,
and the reference.

```bash
uv sync --group dev && uv run pytest              # tests
uv run --group docs properdocs serve              # the docs site
```

## Licence

Apache-2.0.
