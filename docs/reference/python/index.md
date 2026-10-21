# Python API

Generated from the docstrings at build time, one page per module. The names most
programs need are importable from `schemacast` itself:

| Name | What it is |
|---|---|
| `SchemaCaster`, `CastResult`, `EpisodeResult` | the pipeline over many episodes, and what it returns ([`cast`](cast.md)) |
| `Episode`, `read_episodes` | the input ([`episode`](episode.md)) |
| `load_schema` | a graflo schema or manifest from YAML or JSON ([`schema_io`](schema_io.md)) |
| `save_cast` | a cast on disk ([`output`](output.md)) |
| `write_cast`, `load_connection`, `WriteReport` (graflo's `ContainerWriteReport`) | resolving against a database and writing through graflo's `write_record_graphs` ([`write`](write.md)) |
| `Grounding`, `TextSpan`, `UnmappedText` | where an assertion was read, and what no type covers ([`ground`](ground.md)) |
| `JsonlQuarantineSink`, `MemoryQuarantineSink` | where left-out items go ([`sink`](sink.md)) |
| `SchemaCastSettings`, `LLMSettings`, `CacheSettings` | settings ([`config`](config.md)) |

The steps of a cast are modules of their own: [`select`](select.md),
[`prompt`](prompt.md), [`extract`](extract.md), [`validate`](validate.md)
and [`repair`](repair.md); resolution is graflo's `graflo.link.resolve`. The model client is
[`llm`](llm.md), and the benchmark [`bench`](bench.md).
