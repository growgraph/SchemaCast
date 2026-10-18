# Identities and mentions

SchemaCast computes every identity before it builds an edge, the same way graflo
does for any other source. How depends on how the vertex type is identified:

| Identity of the type | Where its key comes from |
|---|---|
| natural (`identity: [email]`) | the text: the model must give the property |
| hash (`hash_identity_properties`) | graflo's digest of those properties |
| assigned (`assigned: true`) | a UUID derived from the schema and its version, the episode's `source_id`, the type and the model's `ref` |
| blank (`blank: true`) | derived the same way |

The derived UUIDs make a replay reproduce the same graph. A fresh model call
that names its refs differently derives new ones, so assigned and blank
vertices are not idempotent across re-casts; natural and hash vertices are.

## Mentions

A text often refers to something it says nothing new about: "the customer with
email jo@example.com raised HD-4415". The model marks such an entity as a
**mention**. A mention is never written as a vertex:

- known by its **primary identity**, it is simply an edge endpoint;
- known only by a **secondary identity** (`secondary_identities` in the
  schema), the edge waits in `pending.jsonl`, and its endpoint is looked up in
  the database at write time. A lookup that finds nothing, or more than one
  vertex, leaves the edge out and is counted in `write.json`.

A mention with neither key is **unresolved**. That includes any mention of an
assigned or blank type ("the work order from last week"): there is nothing to
look it up by. An unresolved mention is never written, since writing it would
mint a duplicate; it is listed in `unresolved.jsonl` with the number of edges
left out because of it, and the episode counts it. A later cast that knows
the entity can resolve it.

## Known entities

The caller may give an episode **candidates**: vertices the graph already
holds that the text may name, each with an id (`cid`), its type, its key and
the label it was found by. SchemaCast never reads the graph; candidates come
from the caller, for instance a lookup by label or alias over the target graph,
or a file (`schemacast cast --known candidates.jsonl`, used for every episode
that carries none of its own).

```json
{"cid": "k1", "type": "Machine", "key": {"serial": "S-7"}, "label": "the big press", "lane": "alias"}
```

The prompt lists them, and the reply may set a vertex's `candidate` to one of
their ids — an enum of exactly those ids. A pick is checked without the model:
an unknown id, or an entity of another type, is rejected and recorded as a
`candidate_rejected` fix, and the vertex falls back to its own key. A valid
pick takes the entity's key, so "the big press is down" links to machine `S-7`
although the text never states a serial. A pick links a mention to an
existing vertex; it never merges two.

Every vertex grounding names how its vertex was identified, in `resolved_by`:
`identity` or `secondary` (a key the text states), `candidate` (a pick, with
the candidate's `lane`) or `minted` (a key SchemaCast derived).

## Edges

An edge is identified by its endpoints and its relation. Give it `identities`
in the schema (`identities: [[source, target, since]]`) only when several
edges between the same two vertices must stay apart; its other properties are
written, never matched on.
