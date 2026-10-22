# The benchmark

`schemacast-bench` casts a corpus of short texts and scores the result against the graph each text
states. No model judges: matching is exact.

```bash
schemacast-bench                              # the oracle row only; free
schemacast-bench --model gpt-5.6-luna         # plus a model row (SCHEMACAST_LLM_* settings)
schemacast-bench --split test --model a --model b
schemacast-bench --model a --replay           # from the cache; makes no request
schemacast-bench compare a.json b.json        # read run b against run a
schemacast-bench --corpus my-corpus/          # a corpus of your own
schemacast-bench score --corpus my-corpus/ casts/*.json   # saved casts; no model
```

Each run writes `<stamp>.json` (counts per episode, rates per case and row) and `<stamp>.md`
under `--out` (default `bench-results/`, ignored by git). Set `SCHEMACAST_BENCH_PRICES` to a JSON
file `{"<model>": {"input_per_1m": …, "output_per_1m": …}, "_source": "…"}` for a cost column;
no prices ship with the package.

## Corpus

`schemacast/bench/cases/<family>/<instance>/` holds `case.yaml` (description, schema, and optionally `recast: true`
or pinned `settings`), `episodes.jsonl` and `expected.yaml`. Schemas live in `schemacast/bench/schemas/`; a case
naming several merges their vertices and edges. Each family isolates one failure mode, in four
domains (`known` in two):

| Family | What it isolates |
|---|---|
| `direction` | same-type relations, passive voice, the target named first |
| `units` | declared units, grouped digits, written-out dates and integers, yes/no flags |
| `identity` | entities the text gives no identity, which must not be written |
| `mention` | entities known by a secondary identity only; earlier assigned or blank entities that must not be minted again; a pick among candidates for the staged ones |
| `recast` | a second cast with a fresh model call |
| `injection` | instructions inside the text |
| `distractors` | a wide schema sliced under a tight budget, with types the text names but says nothing about |
| `selection` | a wide schema sliced under a budget, its types named in inflected forms (`parcels`, `shipped`) and related to each other |
| `context` | a key stated only in an earlier text of the thread (`Episode.context`); everything else in the text itself |
| `known` | a text about a record it never names (`Episode.known`): edges to its ref `k1`…, a restated record that must not be written, entities that cannot be shown; one instance in each half |

`expected.yaml` maps each episode to `vertices` (keyed by a handle), `edges` between handles,
`forbidden` items a cast must not write, and `allowed` items a cast may write without being
required to (an entity the text implies): a produced item matching an `allowed` one counts
neither as matched nor as produced. `forbidden` and `allowed` edges may use the expected
handles; `allowed` handles must differ from them. An edge may end at a known entity by its ref
(`k1`…), which matches by ref and counts as no entity. An optional `pick.yaml` gives, for an episode's staged
mentions (by handle), the candidates a resolver would gather, the entities the record names, and
the expected `pick` (a key, or null when none fits). Every quote occurs in the text (a property may instead
name the `context` item its quote is in, for an identity value only) and every value is what
its field makes of it; a test checks both, and that the oracle scores perfectly on the whole
schema.

`SPLITS.yaml` puts two instances of each family in `dev` and two in `test` (`known`: one in each). Tune against `dev`;
only `--split test` on an unmodified corpus is publishable, and a report says when it is not.
`CORPUS_VERSION` identifies the corpus and `CORPUS_DIGEST` pins it: change a case, a schema or the
split and bump the version, or the test suite fails. Results from different versions are not
comparable.

## A corpus of your own

`--corpus DIR` runs the bench on `DIR/cases/` (with its `SPLITS.yaml`) and `DIR/schemas/`, laid
out like the bundled corpus. A report on such a corpus is never publishable, whatever its digest.

## Scoring saved casts

`schemacast-bench score [--corpus DIR] [--split …] [--row LABEL] CASTS…` scores cast output
already on disk, with no model. Each file holds a `POST /cast` response body, or
`{"episodes": [...], "cast": <body>}`. Episodes pair with the corpus on `source_id`: a corpus
episode no file holds counts as failed and fully missed; a saved episode the corpus does not
hold is listed and ignored. The row is labelled with the casts' model, or `--row`.

A saved cast has no first reply, slice, token counts, re-cast or pick, so conformance,
hallucination, selection, re-cast, pick and token figures read `n/m` (not measured).

Every report ends with *Misses*: per episode, each expected entity and edge nothing matched and
each produced one that matched nothing expected or allowed, by quote.

## Rows

- **oracle** replies with the expected graph through the whole pipeline. It is the ceiling the
  pipeline allows: what selection leaves out of the slice costs it too.
- **a model** row per `--model`, against `SCHEMACAST_LLM_BASE_URL`.

## Metrics

Every metric is a ratio of counts pooled over episodes.

| Metric | Counts |
|---|---|
| Conformance | items of the first reply with no schema finding, over items emitted; *final* is the attempt kept |
| Type / relation / property hallucination | `unknown_type`, `undeclared_edge`, `undeclared_property` findings on the first reply, over vertices, edges, properties emitted |
| Ungrounded | first-reply quotes not found in the text, over items emitted |
| Unfaithful | first-reply numbers or dates their quote does not state (`value_not_in_quote`), over properties emitted |
| Entity, edge, property P/R/F1 | matched over produced and over expected; properties on matched entities and edges only |
| Direction | edges matched, over edges matched or found the wrong way round |
| Span | matched items whose quote overlaps the expected one |
| Mention | matched entities whose `mention` flag agrees |
| Forbidden | forbidden vertices resolution would write, and forbidden edges produced |
| Seed / closure recall | expected types among the seeds and in the slice, read on sliced episodes only |
| Re-cast added / duplicated | natural or hash identities a second cast adds; assigned or blank vertices it mints anew |
| Pick correct / wrong links | staged mentions whose pick links the expected entity (or nothing when none fits), over mentions with a `pick.yaml` entry; links to another entity, over all links |
| Tokens, requests per episode | billed and cache-replayed tokens alike, pick calls included |

Hallucination is measured against the slice the model was shown, so a type selection left out
counts as well; the oracle row separates the two.

Entities match by identity: a natural type's key, a hash type's digest, or any complete secondary
identity. An assigned or blank type matches by equal property values or an overlapping quote.
Values compare after coercion by their field; strings ignore case and runs of whitespace.

## Comparing two runs

`schemacast-bench compare A.json B.json` reads run B against run A. Episodes pair on case and source
id, and only paired episodes count; the rest are listed. For each rate it gives A, B and B − A
over the pooled counts, with a 95% paired bootstrap interval: episodes are resampled together
with their pair, so an interval that spans zero says the corpus cannot tell the two runs apart.
Per episode it counts score rises and falls, tabulates status changes and lists the largest
drops and rises. Across corpus versions, only cases whose content is unchanged are paired (each
case carries a digest); with none in common the comparison is refused. A report from before case
digests (corpus 1.0.0) pairs with a later one only under `--pair-by-name`, which asserts that its
cases did not change. `--row-a` and `--row-b` pick a row
when a report holds more than one model; `--json` also writes the comparison as JSON.

## Adding a case

In a clone: write the three files under a new instance directory, add it to
`SPLITS.yaml`, run `uv run pytest test/test_bench.py`, and set `CORPUS_DIGEST` in
`schemacast/bench/corpus.py` to the value the failing digest test prints, with a
new `CORPUS_VERSION`.
