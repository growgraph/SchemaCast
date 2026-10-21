# Linking staged mentions

A mention its keys do not identify is staged ([Mentions and
resolution](../concepts/identities.md)). graflo can list the entities it may be
(`graflo.link.candidates.gather_candidates`: by an ambiguous alternate key, by
label or alias, or by a relation the text states to a resolved mention; pass
`lookup_text=lambda r: (r.evidence or {}).get("quote")` to look the quote up
too). `pick` chooses among them.

## What a pick does

For one episode, `pick` shows the model the text, its staged mentions and each
mention's candidates, and asks, per mention, for one candidate id or `none`.
Ids are enums of the reply schema, never free text. One call per episode that
has something to choose from; no repair.

Entities the record's metadata names (its speaker, its thread) are passed as
`entities` and offered to every staged mention of their type.

The answer is checked without a model: the candidate must be one listed for
that mention, of the mention's type, and answered once. Anything else is
`rejected` and links nothing.

| Outcome | Means |
|---|---|
| `picked` | linked to `candidate` |
| `none` | the model found no candidate that fits |
| `rejected` | the answer failed the check |
| `no_candidates` | nothing to choose from; no call |
| `failed` | the call failed (`detail` says how) |

**A pick links; it never writes.** `apply_picks` gives a picked mention its
candidate's key and nothing else, so resolving the record graphs again
identifies it by that key and writes none of its stated values onto the entity.
Held relations to it are written then.

## Command line

```bash
schemacast pick --input pick.jsonl --out picks/ [--replay]
```

Each line of `pick.jsonl` is one episode:

```json
{
  "episode": {"source_id": "mail-1", "text": "The customer with email jo@acme.example raised HD-4411."},
  "staged": [{"source_id": "mail-1", "record": {…}, "reason": "unmatched",
              "candidates": [{"vertex_type": "Customer", "key": {"account": "ACC-31"},
                              "label": "Jo Andersson", "lane": "relation",
                              "relation": "RAISED", "anchor": "t"}]}],
  "entities": [{"vertex_type": "Agent", "key": {"handle": "kim"}, "lane": "metadata", "role": "speaker"}],
  "graph": {…}
}
```

`staged` entries are graflo `StagedCandidates`, the staged mention in `record`
(its quote is read from `evidence`); `graph`, the episode's `RecordGraph`, is
optional and names a relation's anchor in the prompt. The run writes
`picks.jsonl` (one pick per staged mention), `episodes.jsonl` and
`manifest.json`. Replies are cached like a cast's: `--replay` (or
`SCHEMACAST_CACHE_REQUIRE_HIT`) reruns from the cache and stops on a miss.

## Python

```python
from schemacast import Picker, PickItem, apply_picks

result = await Picker().pick([PickItem(episode=episode, staged=staged)])
graphs = apply_picks(graphs, result.picks)  # then graflo resolve_records
```
