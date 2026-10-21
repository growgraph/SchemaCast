# Output files

`schemacast cast --out DIR` (or `save_cast(result, DIR)`) writes:

| File | Holds | Same bytes on replay |
|---|---|---|
| `mentions.jsonl` | one record graph per episode: mentions and relations | yes |
| `graph.json` | the schema and what the keys identify: graflo's `GraFloOutput` | yes |
| `staged.jsonl` | mentions not identified, and the relations waiting on them | yes |
| `groundings.jsonl` | one [grounding](../concepts/groundings.md) per mention, relation and property | yes |
| `episodes.jsonl` | one result per episode | yes |
| `quarantine.jsonl` | items left out, failed episodes and `unmapped` entries | yes |
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
| `mentions`, `relations`, `quarantined`, `unmapped` | counts |
| `repair_failure` | why a repair request failed, when one did; the best earlier reply was kept |
| `fixes` | deterministic corrections applied without asking the model: `coerced`, `edge_reversed`, `quote_normalized`, `grounded_in_context` |

## `mentions.jsonl`

One graflo `RecordGraph` per episode cast: its `source_id`, `schema_ref` and
`reference_time`; its `records`, each with a `ref`, a `type`, `may_create`
and the stated `props` (each a `value`); its `relations`, each an `edge_id`
between a `source` and a `target` ref, with props. Every record, relation and
property carries `evidence`: `{"quote": str, "span": {"start": int, "end":
int}}`, with `span.context` the context item's `source_id` when it was read
there. `unmapped` entries are in `quarantine.jsonl`.

## `staged.jsonl`

One graflo record per line. A `StagedRecord` (a `record` key): a mention not
identified, with its `source_id`, the `record` itself and its `reason`
(`partial_key`, `no_key`, `unmatched`, `ambiguous`, `owned_unmatched`,
`keyless_reference`, `hidden`; `matches` counts what an ambiguous lookup
found). A `HeldRelation` (a `relation` key): a relation waiting on staged
endpoints, with the refs it waits on in `waiting_on`. With `--db`, the file
holds what the database did not resolve.

## A pick run

`schemacast pick --out DIR` writes `picks.jsonl` (one record per staged mention:
`source_id`, `ref`, `type`, `outcome`, the `candidate` when `picked`, and a
`detail`), `episodes.jsonl` (per episode: `status` `ok`, `skipped` or `failed`,
requests, counts) and `manifest.json` (versions, model settings, counts by
outcome, usage).

## `manifest.json`

The output format version (5: mentions are graflo record graphs, quotes and spans in `evidence`, `unmapped` outside them, `staged.jsonl` lines without a `record` tag; 4: a cast is mention graphs resolved by keys, and groundings name mention refs; 3: vertex groundings named `resolved_by`; 2: groundings may point into context), the SchemaCast
and graflo versions, the schema as `name@version`, every model
setting that reaches the provider, the token ceiling and slice budget, the
counts by status, and `usage`: calls, cache hits, failures, re-asks, and
tokens, `billed` apart from `replayed`.
