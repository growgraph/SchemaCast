# Support tickets

Seven short texts from a support desk, cast into a small schema: customers, agents, tickets,
products, tags and comments.

| File | Holds |
|---|---|
| `schema.yaml` | the governed schema: identities, a secondary identity, a unit, a blank type |
| `episodes.jsonl` | seven texts with their source id, time and channel |
| `pick.jsonl` | the staged customer of `mail-1042` with two candidates, as `schemacast pick` reads it |

What the episodes exercise:

- `mail-1041` states a customer, a ticket and a product, with typed properties.
- `chat-2207` writes a date in words, and an edge property.
- `mail-1042` names a customer by email only: it is staged, with its edge, until an entity with
  that email is known; and it refers to a ticket by number only.
- `note-88` adds a comment (a blank type), and a wait in hours on a ticket it only refers to.
- `chat-2210` is an instruction, not a statement: nothing should be written from it.
- `chat-2214` is a reply in the thread of HD-4411 and never names it: the episode passes the
  ticket in `known`, and the agent and the tag relate to it as `k1`.
- `mail-1043` denies a product (grounded, never written), plans a handover "on Monday" (a `when`
  resolved against the reference time) and hedges a severity.

Look first; neither command calls a model:

```bash
uv run schemacast sheet --schema examples/tickets/schema.yaml
uv run schemacast select --schema examples/tickets/schema.yaml --episodes examples/tickets/episodes.jsonl
```

Cast, with `SCHEMACAST_LLM_API_KEY` set (and `SCHEMACAST_LLM_BASE_URL` / `SCHEMACAST_LLM_MODEL` for a
local server):

```bash
uv run schemacast cast --schema examples/tickets/schema.yaml \
    --episodes examples/tickets/episodes.jsonl --out out/tickets
```

Write it to a database too with `--db examples/connections/neo4j.yaml`, or to files with
`--db examples/connections/file-backend.yaml`.

Link what stays staged. `pick.jsonl` is the input graflo's candidate gathering would give for a
database where two accounts share `jo.park@example.com`; one call picks one or none:

```bash
uv run schemacast pick --input examples/tickets/pick.jsonl --out out/tickets-picks
```
