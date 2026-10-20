# Preparing a schema

SchemaCast casts into a graflo `Schema`, or the schema block of a `GraphManifest`,
in YAML or JSON. Anything graflo accepts works; a few things decide how well a
model reads it. Check the result with `schemacast sheet --schema schema.yaml`:
that is everything the model sees.

## Describe types in the reader's words

- **`description`** on a vertex or edge is printed in the type sheet. One
  sentence saying what an instance is beats a definition.
- **`semantics.synonyms`** are other names the text may use (`press`,
  `station` for a `Machine`). They are printed in the sheet and they seed
  selection: a text that says "press" pulls in `Machine`.

## Declare keys

Resolution identifies a mention by its type's keys only, so the keys decide
what a text can write ([Mentions and resolution](../concepts/identities.md)).

- A **stated** key (`identity: [serial]`) must be something texts state in full.
  A composite key counts only when every field is stated.
- A **derived** key (`hash_identity_properties`, or an `identity_funnel` whose
  branches are the alternatives) digests properties texts state together.
- A **minted** key (`assigned: true`, `blank: true`) suits things a text creates
  without naming: a note, a work order, a comment.
- An **alternate key** (`secondary_identities`) is how texts name an entity they
  do not key: an email, a handle, a phone number. It finds an entity, never
  creates one.
- **Label fields** — a property whose `semantics.iri` is a label term
  (`rdfs:label`, `skos:prefLabel`, `schema:name`) — mark the names a later
  candidate lookup searches.

A schema from another source maps its keys the same way:

| Source | Primary key | Alternate keys |
|---|---|---|
| SQL | `PRIMARY KEY` | `UNIQUE` constraints |
| Neo4j | node key constraint | uniqueness constraints |
| OWL | the IRI (never in a text) | `owl:hasKey`, inverse-functional datatype properties |
| JSON Schema, OpenAPI | none declared: choose stated fields, derive a key, or mint | fields you know are unique |

graflo's SQL and RDF inference write these as `identity` and
`secondary_identities`. A type whose only key a text cannot state — an IRI, a
surrogate id — needs an alternate key, or its mentions stay staged.

## Type the properties

Every property should have a `type`. Values are coerced to it before writing:

| Type | Accepted from the model |
|---|---|
| `INT`, `UINT` | integers, integral floats, digit strings; not `1,000` |
| `FLOAT`, `DOUBLE` | numbers and numeric strings |
| `BOOL` | `true`/`false`, `yes`/`no`, `1`/`0` |
| `DATETIME` | ISO 8601, kept as a string; `2024` and `2024-03` stay partial |
| `STRING` | anything, stripped |
| `LIST` | a list, or a single value, each item coerced |

A numeric field with `semantics.unit` (`unit: kW`) accepts a value written with
that unit and strips it. A value in any other unit is refused rather than
converted, and units are case-sensitive (`mW` is not `MW`). The sheet shows the
unit, so the model knows what to expect. The model writes numbers plain
(`1250`) and dates as ISO 8601; the quote keeps the text's own form.

## Large schemas

A schema over the slice budget (`SCHEMACAST_SLICE_MAX_ELEMENTS`,
`SCHEMACAST_SLICE_MAX_TOKENS`) is cut down per text: the types the text matches
are seeds, and graflo's `subschema()` closes them into a valid slice, never
dropping an identity field. `schemacast select --episodes episodes.jsonl` shows
each slice and its seeds without calling a model. A text that matches nothing
in a large schema is quarantined as `selection_no_match` rather than cast
against the whole schema.
