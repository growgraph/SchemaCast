# Context

An episode is one short text. Its `context` holds earlier texts of the same
thread, so the model can tell what the text refers to:

```json
{"source_id": "m2", "text": "It is leaking again.",
 "context": [{"source_id": "m1", "text": "Press S-7 was serviced on Monday."}]}
```

The model reads the context; the cast records only what the episode's own text
states. "It" in `m2` can become a mention of press `S-7`, and an edge from
`m2`'s note to it, but nothing `m1` alone says is written.

The one thing context may supply is a key: the identity value (`id:` or
`alt id:`) of an entity the text refers to. Its grounding then has `in_context`
set to the context item's `source_id`, and its offsets point into that item's
text. Any other quote found only in context is a `quote_not_found` finding.

Context is shown to the model as data, like the text, and counts towards
`SCHEMACAST_MAX_EPISODE_TOKENS`. SchemaCast does not assemble it: the caller chooses
the earlier texts, and should keep them few and recent.

## Known entities

A text often belongs to a record it never names: a comment to its ticket, a note
to its sample. The caller passes such records in `known`, identified from the
source:

```json
{"source_id": "c-9", "text": "Reproduced with the AX-200 controller.",
 "known": [{"vertex_type": "Ticket", "key": {"number": "HD-7311"},
            "role": "subject", "label": "Controller reboots after update"}]}
```

| Field | Holds |
|---|---|
| `vertex_type` | its type |
| `key` | its identity: every identity field of the type, each with a value |
| `role` | what it is to the text (lowercase, `_`); `subject` reads "the record this text belongs to", any other "the record's <role>" |
| `label` | optional; shown to the model instead of the key |

At most 8 per episode, none twice. A known entity's type is always in the slice.
It is shown in a `# KNOWN` block between context and text, as data, under a ref
`k1`, `k2`…; an edge the text states may use that ref as `from` or `to`. The model
adds no vertex for it and states none of its properties: a vertex with a `k` ref is
a `known_ref` finding. A known entity whose type the schema lacks, whose type takes
no key from the data, or whose key is not exactly the type's identity fields is not
shown.

Each known entity a kept edge uses is a record of the record graph with ref `k…`,
`may_create: false`, its key as props, and `evidence` `{"known": role, "label":
label}` with no quote or span. It resolves by its key and has no grounding.
