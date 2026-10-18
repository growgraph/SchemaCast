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

A mention with neither is an `identity_insufficient` finding. That includes
any mention of an assigned or blank type ("the work order from last week"):
there is nothing to look it up by. Such an entity is best left out, since
writing it would mint a duplicate; the benchmark's `mention` family measures
how often a model does.

## Edges

An edge is identified by its endpoints and its relation. Give it `identities`
in the schema (`identities: [[source, target, since]]`) only when several
edges between the same two vertices must stay apart; its other properties are
written, never matched on.
