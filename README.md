# SchemaCast <img src="https://raw.githubusercontent.com/growgraph/SchemaCast/refs/heads/main/docs/assets/logo.png" alt="SchemaCast logo" height="32" width="32"/>

**Turn text into a knowledge graph. You say what to look for; every fact keeps the words it came from.**

[![tests](https://img.shields.io/github/actions/workflow/status/growgraph/SchemaCast/tests.yml?branch=main&label=tests)](https://github.com/growgraph/SchemaCast/actions/workflows/tests.yml)
[![pre-commit](https://img.shields.io/github/actions/workflow/status/growgraph/SchemaCast/pre-commit.yml?branch=main&label=pre-commit)](https://github.com/growgraph/SchemaCast/actions/workflows/pre-commit.yml)
[![PyPI](https://img.shields.io/pypi/v/schemacast?color=224777)](https://pypi.org/project/schemacast/)
[![Python](https://img.shields.io/pypi/pyversions/schemacast?color=224777)](https://pypi.org/project/schemacast/)
[![Docs](https://img.shields.io/badge/docs-growgraph.github.io-224777)](https://growgraph.github.io/SchemaCast/)
[![License](https://img.shields.io/badge/license-Apache--2.0-224777)](https://github.com/growgraph/SchemaCast/blob/main/LICENSE)

SchemaCast reads short texts (news, messages, notes, tickets) and writes what they state as a
graph: the entities, how they relate, and what is said about them. A schema of a few lines says
what to look for. Each text is one model call, checked against that schema.

**Status: alpha.** The API may change between minor releases.

## Three steps

**1. Install**

```bash
pip install schemacast
```

**2. Say what to look for**, in a few lines of YAML (`schema.yaml`):

```yaml
name: deals
types:
    Company: {key: name, fields: {name: str, sector: str, city: str}}
    Person: {key: name, fields: {name: str}}
relations:
-   Person CEO_OF Company
-   Company ACQUIRED Company
```

**3. Cast your texts**, one per line (`notes.jsonl`):

```json
{"source_id": "note-1", "text": "Brightline, a payments company in Austin, acquired Oakfield for $40 million."}
{"source_id": "note-4", "text": "Contrary to earlier reports, Raj Patel is not the CEO of Westpeak."}
```

```bash
export SCHEMACAST_LLM_API_KEY=sk-...    # or any OpenAI-compatible endpoint, a local Ollama included
schemacast cast --schema schema.yaml --episodes notes.jsonl --out out/
```

Out comes the graph, and every fact with the words it was read from (as the example's demo
prints it):

```text
note-1  Company   'Brightline, a payments company in Austin'
note-1  name      'Brightline'
note-1  sector    'payments company'
note-1  city      'Austin'
note-1  Company   'Oakfield'
note-1  name      'Oakfield'
note-1  ACQUIRED  'Brightline, a payments company in Austin, acquired Oakfield for $40 million.'
note-1  price     '$40 million'
note-4  Person    'Raj Patel'
note-4  Company   'Westpeak'
note-4  CEO_OF    'Raj Patel is not the CEO of Westpeak'  denied
```

`$40 million` is stored as `price: 40000000.0`, checked as a number. The denial is kept with its
quote and never written to the graph.

No key yet? From a clone, `uv run python examples/hello/demo.py` runs this example on replies
recorded once, for free. Its [ten lines](https://github.com/growgraph/SchemaCast/blob/main/examples/hello/demo.py) are also all it takes from Python.

## What you get

- **Checked facts.** Types, relations and values are checked against the schema; a reply that
  fails is corrected at most twice, then set aside with its reason, never guessed.
- **One entity per key.** "Brightline" in three notes is one company. A mention without its key
  waits until a lookup can identify it, instead of creating a duplicate.
- **Quotes and nuance.** Every fact keeps its quote and character span, and what the text says
  about it: when it holds, whether it is only planned or hedged, and whether the text denies it.
  A denial is kept, never written to the graph.
- **Free reruns.** Replies are cached; `--replay` makes no request and gives the same graph, byte
  for byte.
- **Your database.** `--db conn.yaml` writes to any database [GraFlo](https://github.com/growgraph/graflo)
  supports, Neo4j and ArangoDB among them. Writing a cast twice changes nothing in a database
  that upserts.

## Going further

- **A bigger example**: [`examples/tickets/`](https://github.com/growgraph/SchemaCast/tree/main/examples/tickets), a support desk: replies that
  belong to a ticket without naming it (`known`), mentions that wait for a lookup, and
  `schemacast pick`, which links them to database candidates in one checked call.
- **The full schema format**: the short YAML above is GraFlo's compact notation;
  `schemacast schema --schema schema.yaml` prints the full GraFlo schema it stands for, which
  SchemaCast also reads directly.
- **Plain JSON out**: `cast --bundle` also writes nodes, edges and quotes as JSON Lines with
  stable ids, readable without GraFlo.
- **A service**: `schemacast serve` (extra `server`) offers `POST /cast` and `POST /pick`.

## Benchmark

`schemacast-bench` casts a bundled corpus of texts whose graphs are known and scores the result
exactly, with no model as judge: conformance, hallucination, precision and recall, direction,
spans, mention versus mint, injection, selection and re-cast stability, with tokens and cost. It
runs a corpus of your own the same way (`--corpus DIR`), and scores saved casts with no model
(`schemacast-bench score`).

## Not yet

**Registry mode** is planned: selection across the schemas of a GraFlo schema registry. Today a
cast selects within one schema.

## Documentation

[growgraph.github.io/SchemaCast](https://growgraph.github.io/SchemaCast/): installation, how a cast
works, mentions and resolution, groundings and qualifiers, context and known entities, preparing a
schema, replay, writing to a database, linking staged mentions, running as a service, the
benchmark, and the reference.

```bash
uv sync --group dev && uv run pytest              # tests
uv run --group docs properdocs serve              # the docs site
```

## Licence

Apache-2.0. Maintained by [GrowGraph](https://growgraph.dev).
