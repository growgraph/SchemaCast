# Groundings and quarantine

## Groundings

Every mention, relation and property in a cast has a **grounding**: where in
which text it was read. Groundings are written to `groundings.jsonl` beside the
graph, not into it, and name the record graph's refs; resolution says which
entity a ref became.

| Field | Holds |
|---|---|
| `source_id` | the episode the assertion came from |
| `kind` | `vertex`, `edge` or `property` |
| `vertex`, `ref` | for a mention or its property: the type and the mention's ref |
| `edge`, `source_ref`, `target_ref` | for a relation or its property: the edge and its endpoints' refs |
| `property`, `value` | for a property: its name and the value stated |
| `char_start`, `char_end`, `quote` | the span of the text, and its characters |
| `when` | for a relation or property, when the text says it holds: `quote`, its `span`, `start` and `end` (ISO 8601 date or date-time, `end` null when open) and `granularity` (`instant`, `day`, `week`, `month`, `year`) |
| `modality` | `actual`, `planned` or `hedged`; always `actual` for a mention |
| `negated` | `true` when the text denies the relation or value |
| `in_context` | set when the span is in a [context](context.md) item: its `source_id`; offsets then point into that item's text |
| `extractor`, `model`, `schema_ref` | what produced it: the SchemaCast version, the model, and `name@version` of the schema |
| `reference_time` | the episode's time |

The model gives quotes, never offsets; SchemaCast finds each quote in the text,
exactly first, then ignoring case, accents, runs of whitespace and typographic
quotes and dashes. When a quote occurs more than once, a property takes the
occurrence nearest its vertex's or edge's quote. A quote it cannot find is a
`quote_not_found` finding, so every grounding points at text that is there.

## Qualifiers

The model may qualify each relation and property it returns with `when`,
`modality` and `negated`. It resolves a relative time ("tonight") against the
episode's `reference_time`; SchemaCast checks that the `when` quote is in the
text, that `start` and `end` are ISO 8601 with `start` not after `end`, and
the granularity and modality, and a key property is never negated. A bad
qualifier is an `invalid_qualifier` finding (or `quote_not_found` with detail
`when`), repaired like any other.

A denied relation or property is grounded but is no fact: it stays out of the
record graph that is resolved and written, and is found only in the groundings.
A planned or hedged one stays in the record graph with its qualifiers in
`evidence`. The graph a cast writes carries no qualifiers: a planned relation or
a hedged value is written like an actual one, so a reader that must tell them
apart reads `modality` and `when` from the groundings or the record graph.

## Quarantine

`quarantine.jsonl` holds one record per item left out, per failed episode, and
per `unmapped` entry, in episode order:

| `record` | Means |
|---|---|
| `finding` | an item left out; `kind` is the finding, `fragment` the item as the model wrote it |
| `episode` | the whole episode failed; `kind` says why |
| `unmapped` | something the text states that no type in the slice covers |

A finding record carries the model's raw reply and the number of attempts.
`unmapped` entries show where the schema could grow. An entry whose quote is not
found in the text is dropped.

In Python, any object with an `async write_records(records)` method can be the
sink; `JsonlQuarantineSink` appends to a file, `MemoryQuarantineSink` keeps
records in memory.
