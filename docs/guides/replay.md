# Replay and the cache

Every model reply is cached on disk, keyed on the logical request: the
messages, the reply schema, and every model setting that reaches the provider.
The same episode cast against the same slice with the same settings is the same
request, so it is answered from the cache.

## Replaying a cast

```bash
schemacast cast ... --replay
```

`--replay` (or `SCHEMACAST_CACHE_REQUIRE_HIT=true`) serves every reply from the
cache, and a miss stops the run with an error instead of calling the model. A
replay that finishes therefore made no request. Its mentions, graph, staged
mentions, groundings, quarantine and episode results are byte-identical to the first
run; only `manifest.json` differs, since it reports usage.

A miss means the request changed: the prompt, the schema slice or a model
setting. An episode quarantined as `provider_error` has no cached reply, so its
replay misses too; cast it again without `--replay` to fill the cache.

## Settings

| Variable | Effect |
|---|---|
| `SCHEMACAST_CACHE_DIR` | Where replies are kept. Default `$XDG_CACHE_HOME/schemacast` or `~/.cache/schemacast`. |
| `SCHEMACAST_CACHE_ENABLED` | `false` neither reads nor writes the cache. |
| `SCHEMACAST_CACHE_READ_ONLY` | Serve cached replies, write no new ones. |
| `SCHEMACAST_CACHE_REQUIRE_HIT` | A miss raises; what `--replay` sets. |

## Without a key

With no `SCHEMACAST_LLM_API_KEY`, SchemaCast never calls a provider: a cached reply
is served, and a miss is a configuration error. Sharing a cache directory is
therefore enough to let someone reproduce a run without credentials.

## Token usage

`manifest.json` counts calls, cache hits, failures and tokens. Tokens of live
calls (`billed`) are kept apart from tokens of replies served from the cache
(`replayed`), so a replay reports what the original run consumed without
claiming it was spent again.
