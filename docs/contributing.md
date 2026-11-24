# Contributing to SchemaCast

This page covers what you need to change SchemaCast and get the change merged:
setting up, running the tests, writing documentation, and the checklist a pull
request has to pass.

## Licensing your contribution

SchemaCast is released under the Apache License 2.0. Before we can merge your
first pull request, you need to accept the
[Growgraph Contributor License Agreement](https://github.com/growgraph/SchemaCast/blob/main/CLA.md)
(CLA). You do it once, and it covers all your future contributions to
Growgraph projects. You keep the copyright in your work, and everything you
contribute stays available under an open source license.

When you open a pull request, a bot checks whether you have accepted the CLA.
If you have not, it comments with a link. Read the agreement and reply on the
pull request with:

```
I have read the Growgraph CLA and I accept it.
```

The `cla` check then turns green. You can also sign the agreement on paper:
see its section 11.

**Contributing as part of your job?** Contributions written at work are
welcome. Many employers own what their employees write, though, so before you
contribute, either get your employer's permission or ask them to sign our
Corporate CLA (write to team@growgraph.dev). If you are not sure whether your
employer has a claim, ask them first.

Do not submit code you did not write without telling us its source and
license.

Every commit must be authored with an e-mail address linked to your GitHub
account; otherwise the check cannot match the commit to your acceptance.

## Set up

1. Fork the repository on GitHub and clone your fork.
2. Install the development tools:

    ```bash
    uv sync --group dev
    ```

3. Install the pre-commit hooks:

    ```bash
    uv run pre-commit install
    ```

Run every Python command through `uv run`, so it uses the project's locked
environment. To try a change against an unreleased graflo, overlay a local
checkout without touching the lock:
`uv run --with-editable ../graflo pytest`.

## Run the tests

```bash
uv run pytest
uv run ty check schemacast test
```

This is the run every change has to pass. It needs no model key and no
database: model replies come from fakes in the tests and from replies recorded
with the examples. Two markers are opt-in:

| Marker | Runs with | Needs |
|---|---|---|
| `llm` | `--run-llm` | `SCHEMACAST_LLM_API_KEY`; calls a real model and costs money |
| `neo4j` | `--run-neo4j` | `NEO4J_URI` and `NEO4J_PASSWORD`; **wipes** that database, so point it at a throwaway one |

### Stay light

SchemaCast's runtime dependencies are `graflo`, `openai`, `pydantic`,
`pydantic-settings`, `click` and `pyyaml`. `test/test_dependency_weight.py`
refuses LangChain, langgraph and torch in the installed tree, and numpy,
pandas and rdflib in what `import schemacast` loads. graflo installs some of
these, so import graflo modules that reach them inside the function that needs
them.

### Examples

- `examples/hello/` ships model replies recorded once, in `cache/`, and the
  demo's printout, in `expected.txt`; a test replays them. A change to the
  prompt, the reply schema or the example misses the cache. Record again
  (a paid call per note) with `SCHEMACAST_LLM_API_KEY` set and no other
  `SCHEMACAST_` settings:

    ```bash
    rm -rf examples/hello/cache && uv run python examples/hello/demo.py --record > examples/hello/expected.txt
    ```

- `examples/tickets/` is cast in the tests from hand-written replies in
  `test/data/tickets_replies.json`.

### The benchmark corpus

A changed case, schema or split changes what a score means, so it bumps
`CORPUS_VERSION` and the pinned `CORPUS_DIGEST` in
`schemacast/bench/corpus.py`; a test fails until the digest matches. See
[The benchmark](guides/benchmark.md).

## Measurement lives elsewhere

Documentation, the changelog, docstrings, comments and test names in this
repository describe mechanisms, contracts and defaults. They do not carry
measured results:

- Describe what a setting controls and which direction it moves things. Do not
  quote the number a run produced.
- Name the telemetry a reader uses to measure their own deployment: the run
  manifest, its token usage, `schemacast-bench` reports.
- Do not name an evaluation run, and do not name a test, fixture or flag after
  one; name it after what it guards.

Benchmark reports are not committed. The rule covers claims, not vocabulary:
fixture data such as a sample text or an example schema is exempt.

## Documentation

The documentation is built with ProperDocs:

```bash
uv sync --group dev --group docs
uv run properdocs serve
```

Before you open a pull request, check that the strict build passes:

```bash
uv run properdocs build --strict
```

- Add a new page to the `nav` in `properdocs.yml`.
- The [Python API](reference/python/index.md), [Settings](reference/settings.md)
  and [Command line](reference/cli.md) references are generated at build time
  (`docs/_build/`). Do not write pages for them: add docstrings to a module, a
  `description` to a settings `Field`, and help text to a command.
- Keep `README.md` short and put detail in `docs/`.

## Code style

- Python 3.11 or later, with type hints everywhere.
- `pydantic.BaseModel` for structured data.
- Google-style docstrings on public APIs.
- Follow the naming and patterns of the module you edit.

## Pull request checklist

1. The tests pass: `uv run pytest`, and `uv run ty check schemacast test`.
2. The docs build with `uv run properdocs build --strict` when you changed
   docs, settings, commands or public API.
3. `CHANGELOG.md` has an entry for every user-visible change.
4. The description states the problem and the solution.
5. Every contributor has accepted the CLA: the `cla` check is green.

## Reporting issues

Include your Python, SchemaCast and graflo versions, the steps to reproduce,
what you expected and what happened, and the relevant logs. Leave out your
texts if they are private: a short text that shows the same problem is enough.
