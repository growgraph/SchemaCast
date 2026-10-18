# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- **Renamed from `lpgcast` to `schemacast`**: the distribution, the import package, the CLI
  (`schemacast`, `schemacast-bench`), `SchemaCaster` and `SchemaCastSettings`, the
  `SCHEMACAST_` environment prefixes and the default cache directory. The `text2lpg` alias is gone.
  `serve` reports `schemacast_version`; groundings carry `extractor: schemacast/<version>`.

## [0.2.0] - 2026-10-13

### Added

- **`lpgcast-bench compare A.json B.json`**: run B read against run A episode by episode, with
  pooled rate deltas, paired bootstrap intervals, score rises and falls, and status changes.
  `lpgcast-bench` alone still runs the benchmark.
- **`LLMTruncatedError` and `LLMRefusalError`**: a reply cut off at the output limit or refused
  is raised as such, not re-asked; `UsageTracker` counts both.
- **`EpisodeResult.repair_failure`**: why a repair request failed.
- **`value_not_in_quote` finding**: a number written as digits, or a date, must be what its quote
  states; typed repair as for other findings. The benchmark reports it as *Unfaithful*.
- Repair guidance for a unit other than the declared one: leave the property out, never convert.
- **Identity values ground in context**: a key of an entity the text refers to may be quoted from
  `Episode.context`; its grounding carries `in_context` (the context item's source id) and the
  `grounded_in_context` fix is recorded. `manifest.json` gains `output_format_version` (2).
- A concept page for `Episode.context`.
- **`lpgcast serve`** (the `server` extra: FastAPI, uvicorn): a stateless `POST /cast` taking a
  schema inline, episodes and options, returning graph, groundings, pending edges, quarantine,
  episode results and the manifest; fail-closed bearer key (`LPGCAST_SERVE_API_KEY`), a batch cap,
  `GET /health`. Guide: *Running as a service*.
- **`schema_from_mapping`**: a schema, or a manifest's schema, from a mapping.
- **Benchmark corpus 1.1.0**: `selection` (wide schemas, inflected type names) and `context` (a key
  quoted from an earlier text) families; an expected property may name its context item. Each case
  carries a content digest, and `compare` pairs runs on different corpus versions over unchanged
  cases (`--pair-by-name` for reports from before digests).

### Changed

- A repair that returns the same findings is not repeated.
- Selection stems `-es`, `-ies`, `-ed` and `-ing` beside the plural `-s`, so `shipped` meets
  `ship` and `routing` meets `route`.
- `write_cast` is a thin wrapper over graflo's `write_container`, and `WriteReport` is graflo's
  `ContainerWriteReport`; requires graflo ≥ 1.18.0, whose `subschema()` keeps the edges between
  seeds under a tight budget.
- The prompt asks for plain numbers and ISO 8601 dates, with the quote in the text's own form, so
  `1,250` or `4 March 2024` no longer cost a repair. Cached replies from earlier versions miss.

### Fixed

- A provider fault (rate limit, server error, a second timeout, context length) fails only its
  episode, quarantined as `provider_error`, `truncated` or `refused`; the run goes on.
- A fault during a repair keeps the best earlier reply instead of failing the episode.
- `quarantine.jsonl` is written in episode order, so a replay reproduces it byte for byte.
- Quotes match across typographic quotes and dashes (`“…”`, `’`, `–`, `−`).
- A property's quote takes the occurrence nearest its vertex's or edge's quote, not the first.

## [0.1.1] - 2026-10-11

### Added

- **Package skeleton**: `lpgcast` on Python ≥ 3.11 with six runtime dependencies (`graflo`, `openai`,
  `pydantic`, `pydantic-settings`, `click`, `pyyaml`).
- **Settings** from `LPGCAST_LLM_*`, `LPGCAST_CACHE_*` and `LPGCAST_*` variables only; no `.env` file
  is read. The default model is `gpt-5.6-luna`.
- **`LLMClient.acomplete_json`**: one JSON object per request from any OpenAI-compatible endpoint;
  strict `json_schema` output with a `json_object` fallback, tolerant parsing, re-ask on malformed
  JSON, one re-issue on timeout, typed configuration errors.
- **Response cache** keyed on the logical request, written atomically; read-only mode and
  `LPGCAST_CACHE_REQUIRE_HIT` for replays that make no request. Without an API key only cached
  responses are served.
- **Usage accounting**: calls, failures and tokens, with billed tokens kept apart from tokens
  replayed from the cache; `track_usage()` scopes it per task.
- **CLI** `lpgcast` (alias `text2lpg`) with `sheet`: the type sheet, or with `--json-schema` the
  reply schema, for a schema or manifest file.
- **Episodes** (`Episode`, `read_episodes`): text, source id, reference time, metadata (never shown
  to the model) and thread context, from JSON Lines.
- **Schema selection** (`select`): exact-form, phrase and number-unit lanes fused by rank, a relative
  relevance gate, and closure by graflo's `subschema()` under a budget; a schema under budget passes
  whole, and a large schema nothing matches is a `SelectionError` with a reason.
- **Extraction prompt** (`build_messages`): byte-deterministic, type sheet and rules first, the text
  inside a tagged block it cannot close.
- **`extract`**: one call per episode in the flat reply format, parsed into wire models; an episode
  over `LPGCAST_MAX_EPISODE_TOKENS` is refused before any call.
- **CLI** `lpgcast select`: the slice each text would be cast against, as JSON lines or type sheets.
- **Validation** (`validate`): unknown types and relations, properties not declared on their type,
  values that do not coerce to their field type, insufficient identities, dangling refs and quotes
  not found in the text are findings; values are coerced (declared units stripped), reversed edges
  flipped and quotes matched ignoring case, accents and whitespace, each recorded as a fix.
- **Typed repair**: at most two follow-up requests listing the findings by kind; the attempt that
  keeps the most items wins.
- **Quarantine sink** (`JsonlQuarantineSink`): left-out items, failed episodes and `unmapped`
  entries, with the raw reply.
- **Identity resolution and assembly** (`resolve`): natural keys, digests for hash and funnel types,
  UUIDs derived from schema, episode, type and ref for assigned and blank types; mentions are never
  written as vertices, and an edge to a mention known only by a secondary identity waits for a lookup.
- **Groundings**: one per vertex, edge and property, with character offsets found from the quote.
- **`LPGCaster`**: the whole pipeline over many episodes, concurrently; a misconfigured model or a
  cache miss under replay stops the run.
- **`write_cast`**: defines the schema on the target (reusing an existing one), looks up
  secondary-identity mentions and upserts through graflo's writer.
- **CLI** `lpgcast cast`: graph, groundings, quarantine, episode results and a run manifest under
  `--out`; `--db` writes to a graflo connection; `--replay` makes no request.
- **`LPGCaster.cast_episode`** returns an `EpisodeOutcome` that also carries the slice, the
  first reply as checked and the attempt kept.
- **Benchmark** `lpgcast-bench`: a versioned corpus of seven families in four domains with a
  dev/test split and a pinned digest; exact scoring with no model as judge; an oracle row; a
  fresh-call re-cast; JSON and Markdown reports with token counts and optional prices.
- **Top-level API**: `from lpgcast import LPGCaster, load_schema, read_episodes, write_cast, ...`;
  names load on first use, so `import lpgcast` stays light.
- **Documentation site**: installation, quick start, how a cast works, identities and mentions,
  groundings and quarantine, guides to schemas, replay, databases and the benchmark, and a
  reference whose settings, command-line and Python pages are generated from the code.
- **Examples**: `examples/tickets/` (a support-desk schema and five texts) and
  `examples/connections/` (file backend and Neo4j).
- **CI**: tests (with a check that a plain install imports and stays light), pre-commit, docs to
  GitHub Pages, and PyPI publishing on release once enabled.
