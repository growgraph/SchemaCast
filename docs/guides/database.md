# Writing to a database

`schemacast cast --db conn.yaml` writes the graph to any database graflo
supports, after saving it under `--out`. From Python, call
`await write_cast(result, load_connection("conn.yaml"))`.

## Connection files

A connection file is a graflo connection config: `db_type` picks the database,
the other keys are its fields. Fields the file leaves out are read from the
environment, so keep passwords there.

=== "Neo4j"

    ```yaml
    db_type: neo4j
    uri: bolt://localhost:7687
    username: neo4j
    ```

    with `NEO4J_PASSWORD` set.

=== "graflo file backend"

    ```yaml
    db_type: graflo_backend
    output_dir: db/
    ```

Both are in `examples/connections/` of the repository. graflo's
[database connections guide](https://growgraph.github.io/graflo/guides/database_connections/)
lists every backend and field.

## What a write does

1. **Defines the schema** on the target, or reuses the definition already
   there. `--recreate-schema` drops and redefines it first; on Neo4j that
   empties the database.
2. **Looks up mentions** known only by a secondary identity, and attaches the
   edges that waited for them. A lookup that finds nothing, or more than one
   vertex, leaves its edge out.
3. **Upserts** vertices by identity and edges by endpoints and relation (plus
   any `identities` the edge declares), through graflo's writer.

`write.json` in the output directory records the target, whether the schema was
defined or reused, the documents sent per type, and how many lookups resolved.

## Writing twice

On a database that upserts, writing the same cast twice changes nothing:
natural and hash identities match, and a replayed cast derives the same keys
for assigned and blank vertices. A fresh model call may name its refs
differently and so derive new keys for those; see [Identities and
mentions](../concepts/identities.md).

graflo's file backend appends on every write instead of upserting. Count
distinct identities there, not records.
