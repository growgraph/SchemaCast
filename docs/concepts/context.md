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
