# Preparing a schema

SchemaCast casts into a graflo `Schema`, or the schema block of a `GraphManifest`,
in YAML or JSON. Anything graflo accepts works; a few things decide how well a
model reads it. Check the result with `schemacast sheet --schema schema.yaml`:
that is everything the model sees.

To start without learning graflo's format, write the schema in the
[compact notation](#the-compact-notation) below; it is read as the graflo
schema it stands for.

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

## The compact notation

A shorter way to write a schema, for getting started: graflo's compact notation
(`graflo.interop.compact`). It covers types, their descriptions, other names,
keys, alternate keys and typed fields, and relations with fields. It is read as
the graflo schema it stands for, so every command and `POST /cast` take it as
they take a graflo schema. A file with `types` at the top is read as compact.

```yaml
name: team
version: 1.0.0
types:
  Person:
    about: A person.
    key: email
    alt_keys: [name]
    fields:
      email: str
      name: str
      role: {type: str, about: "What the person does, e.g. engineer."}
  Company:
    aka: [firm, employer]
    key: name
    fields: {name: str, founded: int}
relations:
  - Person WORKS_AT Company
  - {from: Person, name: MANAGES, to: Person, fields: {since: datetime}}
```

| Key | Means | In graflo |
|---|---|---|
| `name`, `version`, `about` | the schema | `metadata` |
| `types.<T>.about`, `aka` | what an instance is, other names | `description`, `semantics.synonyms` |
| `key` | a field, or a list of fields, that identifies an entity | `identity` |
| `hashed: true` | the key is a value, not a name: the identity is its digest (a tag, a label) | `hash_identity_properties` |
| no `key` | each mention is a new entity (a comment, a note) | `blank: true` |
| `alt_keys` | other keys: a list (each named `by_<fields>`) or a mapping name → fields | `secondary_identities` |
| `fields.<f>` | `str`, `int`, `float`, `bool`, `datetime`, `uuid` or `any`; or `{type, about, aka, unit}` | `properties` |
| `relations` | `"Source RELATION Target"`, or `{from, name, to, about, aka, fields}` | `edge_config.edges` |

Quote a description that holds `: ` or `, ` inside braces, and a relation named
like a YAML boolean (`ON`, `YES`) in the long form.

The notation is a subset: a schema that needs more (inverses, edge identities,
filters, IRIs, other field types) is written in graflo's format. Move between
the two with:

```bash
schemacast schema --schema schema.yaml              # the graflo schema it stands for
schemacast schema --schema schema.yaml --to compact # back; refused, naming what is lost, when it does not fit
```
