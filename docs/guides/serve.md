# Running as a service

`schemacast serve` casts over HTTP. It suits a platform that holds the schema and
writes the graph itself: the service receives the schema with the episodes,
casts them, and returns the result. It never writes to a database and never
calls back, so it holds no database credentials.

```bash
pip install 'schemacast[server]'
export SCHEMACAST_SERVE_API_KEY=…            # required; see below
export SCHEMACAST_LLM_MODEL=… SCHEMACAST_LLM_API_KEY=…
schemacast serve --port 8997
```

## `POST /cast`

```json
{
  "schema": {"metadata": {…}, "core_schema": {…}},
  "episodes": [{"source_id": "m1", "text": "Ann operates press S-7."}],
  "options": {"user_instruction": "…", "slice_max_elements": 40}
}
```

`schema` is a graflo schema, or a manifest carrying one. `options` may set
`user_instruction`, `slice_max_tokens`, `slice_max_elements` and
`max_episode_tokens`; anything left out takes the service's
[setting](../reference/settings.md). A request cannot choose the model or its
endpoint.

The reply holds what [`schemacast cast --out`](../reference/output.md) writes:
`mentions` (one mention graph per episode), `graph` (graflo's `GraFloOutput` of
what the keys identify without a database), `staged`, `groundings`,
`quarantine`, `episodes` and `manifest`. To write, pass `mentions` to graflo's
`write_mention_graphs`, the function `write_cast` uses: it resolves them
against the target first.

| Status | Means |
|---|---|
| 200 | cast; an episode that failed is in `episodes` and `quarantine`, not an error |
| 401 | missing or wrong bearer token |
| 413 | more episodes than `SCHEMACAST_SERVE_MAX_EPISODES` |
| 422 | the body, or its `schema`, is not valid |
| 503 | no key configured, or the model is misconfigured (no key, unknown model) |

The request is synchronous: the reply comes when every episode is cast. Keep
batches small enough for the caller's timeout.

## Authentication

Every route but `GET /health` needs `Authorization: Bearer
$SCHEMACAST_SERVE_API_KEY`. Without a configured key the service refuses to cast
(503) rather than serve anyone who can reach it; set
`SCHEMACAST_SERVE_ALLOW_ANONYMOUS=true` only on a development machine.

`GET /health` reports the versions, whether a key is configured and the batch
cap.
