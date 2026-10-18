# Output files

`schemacast cast --out DIR` (or `save_cast(result, DIR)`) writes:

| File | Holds | Same bytes on replay |
|---|---|---|
| `graph.json` | the schema and the graph: graflo's `GraFloOutput` | yes |
| `groundings.jsonl` | one [grounding](../concepts/groundings.md) per vertex, edge and property | yes |
| `pending.jsonl` | edges waiting for a secondary-identity lookup at write time | yes |
| `episodes.jsonl` | one result per episode | yes |
| `quarantine.jsonl` | items left out, failed episodes and `unmapped` entries | yes |
| `unresolved.jsonl` | mentions with no key and no picked entity, never written | yes |
| `manifest.json` | versions, settings, counts and token usage | no: usage differs |
| `write.json` | what was sent to the database, with `--db` | — |

JSON is written with sorted keys, so two casts compare with `diff`.

## `episodes.jsonl`

| Field | Holds |
|---|---|
| `source_id` | the episode |
| `status` | `ok` (nothing left out), `partial` (some items quarantined) or `failed` (nothing written) |
| `failure` | why a failed episode failed |
| `selection` | `whole` or `slice` |
| `retrieval` | how selection went: hits per lane, seeds fused, gated out, capped and kept, and the schema's size |
| `requests`, `repairs` | model requests, re-asks and repairs included; typed repair rounds |
| `vertices`, `edges`, `pending`, `quarantined`, `unmapped`, `unresolved` | counts |
| `repair_failure` | why a repair request failed, when one did; the best earlier reply was kept |
| `fixes` | deterministic corrections applied without asking the model: `coerced`, `edge_reversed`, `quote_normalized`, `grounded_in_context`, `candidate_rejected` |

## `pending.jsonl`

An edge with an endpoint known only by a secondary identity: its `edge_id`, its
`source` and `target` (each a vertex type with either an `identity` or a
`match` on the secondary key) and its properties.

## `unresolved.jsonl`

A mention the text gives no key for and the reply picked no known entity for:
its episode (`source_id`), `ref`, `type`, `quote` and character `span`, and
`held_edges`, the edges left out because they reach it. Nothing of it is
written; a later cast with candidates for it can resolve it.

## `manifest.json`

The output format version (3: vertex groundings name `resolved_by`, and unresolved mentions have their own file; 2: groundings may point into context), the SchemaCast
and graflo versions, the schema as `name@version`, every model
setting that reaches the provider, the token ceiling and slice budget, the
counts by status, and `usage`: calls, cache hits, failures, re-asks, and
tokens, `billed` apart from `replayed`.
