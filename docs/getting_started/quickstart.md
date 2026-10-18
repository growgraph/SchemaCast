# Quick start

This walks through the example in `examples/tickets/` of the repository: five
texts from a support desk and a small schema of customers, agents, tickets,
products, tags and comments.

## 1. Look at what the model will see

```bash
schemacast sheet --schema examples/tickets/schema.yaml
```

This prints the **type sheet**: every vertex type with its description, other
names and properties, how it is identified, and every edge as
`(Source)-[RELATION]->(Target)`. It is all the model learns about the schema.
No model is called.

```bash
schemacast select --schema examples/tickets/schema.yaml --episodes examples/tickets/episodes.jsonl
```

This shows the slice of the schema each text would be cast against. A small
schema passes whole; a large one is cut down to the types the text is about.

## 2. Write the texts as episodes

An episodes file holds one JSON object per line:

```json
{"source_id": "mail-1041", "text": "Customer ACC-2291 (Jo Park) opened ticket HD-4411 ...", "reference_time": "2026-03-02T09:14:00Z", "metadata": {"channel": "email"}}
```

`source_id` identifies the record the text came from, `reference_time` resolves
dates such as "yesterday", and `metadata` is kept with the record but never
shown to the model.

## 3. Cast

With a model configured (see [Installation](installation.md)):

```bash
schemacast cast --schema examples/tickets/schema.yaml \
    --episodes examples/tickets/episodes.jsonl --out out/tickets
```

A one-line summary counts the texts cast whole (`ok`), in part (`partial`) or
not at all (`failed`). Under `out/tickets/`:

| File | Holds |
|---|---|
| `graph.json` | the schema and the graph |
| `groundings.jsonl` | the quote and character span of every vertex, edge and property |
| `quarantine.jsonl` | what was left out and why, with the model's reply |
| `pending.jsonl` | edges to an entity named only by a secondary identity |
| `episodes.jsonl` | one result per text |
| `manifest.json` | versions, settings, counts and token usage |

The last text of the example, an instruction to change every ticket, should
produce nothing: the text is data to the model, and anything it does write must
pass the schema.

## 4. Write to a database

```bash
schemacast cast ... --db examples/connections/neo4j.yaml
```

A connection file is a graflo connection config; see
[Writing to a database](../guides/database.md).

## 5. Run it again for free

```bash
schemacast cast ... --out out/tickets-again --replay
```

Every reply comes from the cache: no request is made, and everything but
`manifest.json` is byte-identical. See [Replay and the cache](../guides/replay.md).

## From Python

```python
import asyncio

from schemacast import SchemaCaster, load_schema, read_episodes, save_cast

schema = load_schema("examples/tickets/schema.yaml")
result = asyncio.run(
    SchemaCaster(schema).cast(read_episodes("examples/tickets/episodes.jsonl"))
)

print(result.manifest.status)  # {'ok': ..., 'partial': ..., 'failed': ...}
save_cast(result, "out/tickets")
```

`result.output` is graflo's `GraFloOutput`: the schema with one `GraphContainer`
of vertices and edges.
