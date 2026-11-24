# The neutral bundle

`schemacast cast --out DIR --bundle` (or `save_bundle(result, DIR)`) also writes
`DIR/bundle/`: the cast as plain JSON Lines, read with any JSON library, no
graflo needed. The graflo files under `DIR` are written as always; the bundle
sits beside them.

| File | One line or document per |
|---|---|
| `nodes.jsonl` | node: `{id, type, key, properties}` |
| `edges.jsonl` | edge: `{id, relation, source: {type, id}, target: {type, id}, properties}` |
| `groundings.jsonl` | [grounding](../concepts/groundings.md), with `node_id` or `edge_id` |
| `staged.jsonl` | mention not identified: `{source_id, ref, type, stated, reason}` |
| `schema.yaml` | the schema, in the [compact notation](../guides/schema.md#the-compact-notation) when it fits, else graflo's |
| `bundle.json` | `bundle_format_version`, producer versions, `schema_ref`, `schema_notation`, counts, files |

## Ids

A node's `id` is a digest of its type and key (its identity fields and values),
so the same entity has the same id in every cast. An edge's `id` is a digest of
its relation, its endpoints' ids and the values of the edge's identity fields.
Two texts stating one relation give one edge; later statements update its
properties.

## Joining text to graph

A grounding carries `node_id` (a mention or its property) or `edge_id` (a
relation or its property) when that node or edge is in the bundle. It carries
none when the item is not a fact of the graph:

- a denied relation or value (`negated: true`);
- a mention not identified yet, or a relation waiting on one: these carry
  `staged: true`, and the mention is in `staged.jsonl`.

A planned or hedged relation is an edge like any other; its grounding says
`modality` and `when`.

## Shapes

Every line's shape is in `neutral.schema.json`, a JSON Schema shipped in the
package (`schemacast.neutral.SCHEMA_FILE`), one `$defs` entry per file. A
change to a shape bumps `bundle_format_version`.
