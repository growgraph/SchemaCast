# Quick start

Three steps: install, say what to look for, cast your texts. The example is in
[`examples/hello/`](https://github.com/growgraph/SchemaCast/tree/main/examples/hello):
five short news notes about made-up companies, turned into a graph of who runs
what and who bought whom.

## 1. Install

```bash
pip install schemacast
```

To run the example as it is, clone the repository:

```bash
git clone https://github.com/growgraph/SchemaCast && cd SchemaCast && uv sync
```

## 2. Say what to look for

A schema names the kinds of things in your texts and how they relate. This one
has two types and two relations:

```yaml
--8<-- "examples/hello/schema.yaml"
```

`key` is what makes two mentions the same entity: "Brightline" in two notes is
one company. `fields` are what to read about each one, with a type the value is
checked against. That is all a first schema needs; [Preparing a
schema](../guides/schema.md) covers the rest.

## 3. Cast your texts

One text per line, with an id for where it came from and the time it was written
(so "next year" means something):

```json
--8<-- "examples/hello/episodes.jsonl"
```

With a model key:

```bash
export SCHEMACAST_LLM_API_KEY=sk-...
schemacast cast --schema examples/hello/schema.yaml \
    --episodes examples/hello/episodes.jsonl --out out/hello
```

No key? The example ships the model's replies, recorded once, so you can see the
result first, for free:

```bash
uv run python examples/hello/demo.py
```

Any OpenAI-compatible endpoint works, a local Ollama included; see
[Installation](installation.md#pointing-it-at-a-model).

## What comes out

Every fact, each with the words it was read from:

```text
--8<-- "examples/hello/expected.txt"
```

- **One entity per key.** Brightline appears in three notes and is one company;
  note 2 adds to what note 1 said about Oakfield.
- **Plans are marked.** "plans to buy Westpeak" is an acquisition marked
  `planned`, and "next year" is read as 2027 from the date of the note.
- **Denials are kept apart.** "Raj Patel is not the CEO" is quoted, marked
  `denied`, and never written to the graph.
- **Dates are read.** "in 2023" is kept as when Ana Ruiz's role holds.
- **Values are typed.** "$40 million" is stored as `price: 40000000.0`, checked
  as a number.

Under `out/hello/`, `graph.json` holds the graph and `groundings.jsonl` the
quote and character span of every fact; [Output files](../reference/output.md)
lists the rest.

## The same from Python

The demo is the whole program:

<!-- fmt: off -->
```python
--8<-- "examples/hello/demo.py"
```
<!-- fmt: on -->

`result.output` holds the graph; `result.groundings` the quotes.

## Next steps

- **Write it to a database**: add `--db connection.yaml` (Neo4j, ArangoDB, and
  the rest graflo supports). See [Writing to a database](../guides/database.md).
- **Run it again for free**: `--replay` serves every reply from the cache and
  makes no request. See [Replay and the cache](../guides/replay.md).
- **See what the model is shown**: `schemacast sheet --schema schema.yaml`, no
  model call.
- **Plain JSON out**: `--bundle` also writes nodes and edges as JSON Lines with
  stable ids. See [The neutral bundle](../reference/bundle.md).
- **A bigger example**: [`examples/tickets/`](https://github.com/growgraph/SchemaCast/tree/main/examples/tickets),
  a support desk, shows texts that belong to a thread without naming it
  ([known entities](../concepts/context.md#known-entities)), mentions that wait
  for a lookup, and [linking them](../guides/pick.md).
- **How it works**: [How a cast works](../concepts/index.md).
