# Groundings and quarantine

## Groundings

Every vertex, edge and property in a cast has a **grounding**: where in which
text it was read. Groundings are written to `groundings.jsonl` beside the
graph, not into it.

| Field | Holds |
|---|---|
| `source_id` | the episode the assertion came from |
| `kind` | `vertex`, `edge` or `property` |
| `vertex`, `identity` | for a vertex or its property: the type and identity (or lookup key) |
| `edge`, `source_identity`, `target_identity` | for an edge or its property |
| `property` | for a property: its name |
| `char_start`, `char_end`, `quote` | the span of the text, and its characters |
| `in_context` | set when the span is in a [context](context.md) item: its `source_id`; offsets then point into that item's text |
| `extractor`, `model`, `schema_ref` | what produced it: the SchemaCast version, the model, and `name@version` of the schema |
| `reference_time` | the episode's time |

The model gives quotes, never offsets; SchemaCast finds each quote in the text,
exactly first, then ignoring case, accents, runs of whitespace and typographic
quotes and dashes. When a quote occurs more than once, a property takes the
occurrence nearest its vertex's or edge's quote. A quote it cannot find is a
`quote_not_found` finding, so every grounding points at text that is there.

## Quarantine

`quarantine.jsonl` holds one record per item left out, per failed episode, and
per `unmapped` entry, in episode order:

| `record` | Means |
|---|---|
| `finding` | an item left out; `kind` is the finding, `fragment` the item as the model wrote it |
| `episode` | the whole episode failed; `kind` says why |
| `unmapped` | something the text states that no type in the slice covers |

A finding record carries the model's raw reply and the number of attempts.
`unmapped` entries show where the schema could grow.

In Python, any object with an `async write_records(records)` method can be the
sink; `JsonlQuarantineSink` appends to a file, `MemoryQuarantineSink` keeps
records in memory.
