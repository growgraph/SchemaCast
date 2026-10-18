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

## Make identities extractable

- A **natural** identity (`identity: [serial]`) must be something texts state;
  a vertex whose identity the text does not give is left out.
- Use **`hash_identity_properties`** when the key is a combination of
  properties the text gives.
- Use **`assigned: true`** or **`blank: true`** for things a text creates
  without naming: a note, a work order, a comment.
- Declare **`secondary_identities`** for keys texts use to refer to an entity
  they do not otherwise identify (an email, a phone number, a badge). Such a
  mention is looked up at write time; see [Identities and
  mentions](../concepts/identities.md).

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
