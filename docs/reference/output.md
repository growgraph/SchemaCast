# Output files

`schemacast cast --out DIR` (or `save_cast(result, DIR)`) writes:

| File | Holds | Same bytes on replay |
|---|---|---|
| `mentions.jsonl` | one mention graph per episode: mentions, relations, unmapped | yes |
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

One mention graph per episode cast: its `source_id`, `schema_ref` and
`reference_time`; its `mentions`, each with a `ref`, a `type`, `introduces`,
the `quote` and `span` it was read at and the stated `props` (each a `value`,
`quote` and `span`); its `relations`, each an `edge_id` between a `source` and
a `target` ref, with a quote, span and props; and its `unmapped` entries.

## `staged.jsonl`

One record per line. `"record": "mention"`: a mention not identified, with its
`source_id`, the `mention` itself and its `reason` (`partial_key`, `no_key`,
`unmatched`, `ambiguous`, `owned_unmatched`, `keyless_reference`; `matches`
counts what an ambiguous lookup found). `"record": "relation"`: a relation
waiting on staged endpoints, with the refs it waits on in `waiting_on`. With
`--db`, the file holds what the database did not resolve.

## `manifest.json`

The output format version (4: a cast is mention graphs resolved by keys, and groundings name mention refs; 3: vertex groundings named `resolved_by`; 2: groundings may point into context), the SchemaCast
and graflo versions, the schema as `name@version`, every model
setting that reaches the provider, the token ceiling and slice budget, the
counts by status, and `usage`: calls, cache hits, failures, re-asks, and
tokens, `billed` apart from `replayed`.
