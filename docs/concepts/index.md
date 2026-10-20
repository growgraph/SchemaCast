# How a cast works

A **cast** turns episodes — short texts with their source metadata — into
mention graphs: what each text names, what it states about it and the
relations between, with a grounding for every assertion. graflo then resolves
the mentions to entities by their keys and writes what it identifies. Each
episode goes through the same steps, and episodes run concurrently.

| Step | What happens |
|---|---|
| **Select** | The text is matched against the schema's names, other names and units, and the best matches seed graflo's `subschema()`, which closes them into a valid slice under a budget. A schema under budget passes whole. |
| **Prompt** | The slice is rendered as a type sheet. The text follows inside a delimited block, after a sentence saying it is data, not instructions. The same episode and slice always give the same bytes. |
| **Extract** | One model call. The reply is flat: a list of vertices (each with a `ref`, a `type`, a `quote` and name–value properties), a list of edges between refs, and `unmapped` for what no type covers. |
| **Validate** | Every item is checked against the slice and the text. Values are coerced to their field type, an edge given backwards is turned round, and quotes are matched ignoring case, accents and whitespace; each fix is recorded. What is still wrong is a finding. |
| **Repair** | If there are findings, the model is shown them, grouped by kind, and asked for its whole reply again; at most twice. The attempt that keeps the most items wins. |
| **Quarantine** | What still fails is left out, with the finding and the model's reply, and written to the quarantine sink. The rest of the reply stands. |
| **Mention graph** | What remains becomes the episode's mention graph, each mention, relation and property with its grounding. |
| **Resolve** | graflo identifies each mention by its keys, across the batch's texts and, with a database, in the graph; the rest is staged with the reason. See [Mentions and resolution](identities.md). |
| **Write** | Optionally, what resolved is upserted into a database through graflo's writer. See [Writing to a database](../guides/database.md). |

## Light by contract

One call per episode, at most two repairs, and nothing more: no critic pass, no
chunking, no encoder and no vector store. An episode longer than
`SCHEMACAST_MAX_EPISODE_TOKENS` is refused before any call, not split.

## The checks

| Finding | The item it removes |
|---|---|
| `unknown_type` | a vertex whose type is not in the slice |
| `duplicate_ref` | a second vertex with the same `ref` |
| `identity_insufficient` | nothing: a mention the text introduces with no complete key is kept, and one repair asks for a key the reply may have left out (see [Mentions and resolution](identities.md)) |
| `undeclared_edge` | an edge whose relation the slice does not declare between those types, either way round |
| `dangling_ref` | an edge to a `ref` no vertex has |
| `undeclared_property` | a property its type does not declare |
| `duplicate_property` | a property given twice |
| `coercion` | a value its field type refuses, or a unit other than the declared one |
| `value_not_in_quote` | a number written as digits, or a date, that its quote does not state (`37.5` on "3.75 kW", `2024-03-01` on "March 2024"); spelled-out and relative values are not checked |
| `quote_not_found` | an item whose quote is not in the text |
| `endpoint_invalid` | an edge whose endpoint was removed; never shown to the model, it goes when the endpoint is fixed |

A repair that returns the same findings is not repeated: the slice does not let
the model fix them. A repair request that fails keeps the best earlier reply and
records why in the episode's `repair_failure`.

A failure of the whole episode is recorded with its reason: the selection found
nothing (`selection_*`), the text is over the ceiling (`too_long`), the provider
rejected or dropped the request (`provider_error`), the reply stopped at the
output limit (`truncated`), the model refused (`refused`), the reply was not
JSON (`unparseable`) or not the reply shape (`wire_shape`), or nothing in it
survived (`nothing_valid`). A truncated or refused reply is not re-asked. A model
that is misconfigured, an endpoint not reached by any request of the run, or a
cache miss while replaying, stops the run instead: every other episode would
fail the same way.

`schemacast cast` exits with 0 when every failure is about the episode itself,
since a rerun replays the same reply from the cache; with 75 when some episodes
failed at the provider (`provider_error`), since a rerun retries exactly those;
and with 1 when the run stopped or every episode failed.

## Beside OntoCast

OntoCast builds and evolves an ontology from documents and writes RDF; SchemaCast
casts short, frequent texts into a property-graph schema you already govern.
