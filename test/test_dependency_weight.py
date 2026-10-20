"""SchemaCast stays light: six runtime dependencies and a small import footprint."""

import re
import subprocess
import sys
from importlib.metadata import distributions, requires

RUNTIME = {"click", "graflo", "openai", "pydantic", "pydantic-settings", "pyyaml"}

#: Never installed alongside SchemaCast.
NOT_INSTALLED = ("langchain", "langgraph", "torch")

#: Never loaded by importing SchemaCast. graflo installs some of these for its own
#: database and RDF features; SchemaCast's import path must not reach them.
NOT_IMPORTED = (
    "langchain",
    "langchain_core",
    "langgraph",
    "torch",
    "numpy",
    "pandas",
    "rdflib",
    "sklearn",
)


def _name(requirement: str) -> str:
    return re.split(r"[\s<>=!~;\[]", requirement, maxsplit=1)[0].lower()


def test_declares_exactly_the_runtime_dependencies():
    declared = {
        _name(req) for req in requires("schemacast") or [] if "extra ==" not in req
    }
    assert declared == RUNTIME


def test_heavy_frameworks_are_not_installed():
    installed = {(dist.metadata["Name"] or "").lower() for dist in distributions()}
    offending = sorted(
        name
        for name in installed
        if any(
            name == banned or name.startswith(f"{banned}-") for banned in NOT_INSTALLED
        )
    )
    assert offending == []


def test_import_loads_nothing_heavy():
    probe = (
        "import sys, schemacast, schemacast.cli, schemacast.config, schemacast.llm, schemacast.episode, "
        "schemacast.select, schemacast.prompt, schemacast.extract, schemacast.validate, schemacast.repair, "
        "schemacast.ground, schemacast.sink, schemacast.cast, schemacast.output, schemacast.write, "
        "schemacast.schema_io, schemacast.bench.cli, schemacast.bench.corpus, schemacast.bench.score, "
        "schemacast.bench.oracle, schemacast.bench.run, schemacast.bench.report, "
        "graflo.hq.mention_resolve\n"
        f"print(','.join(m for m in {NOT_IMPORTED!r} if m in sys.modules))"
    )
    loaded = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    ).stdout.strip()
    assert loaded == ""


def test_the_top_level_names_resolve_lazily():
    probe = (
        "import sys, schemacast\n"
        "assert 'schemacast.cast' not in sys.modules\n"
        "from schemacast import SchemaCaster, load_schema, read_episodes, write_cast\n"
        "assert 'schemacast.cast' in sys.modules\n"
        "print(','.join(m for m in "
        f"{NOT_IMPORTED!r} if m in sys.modules))"
    )
    loaded = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )
    assert loaded.stdout.strip() == ""


def test_every_lazy_name_is_exported_and_resolves():
    import schemacast

    assert set(schemacast.__all__) == {"__version__", *schemacast._EXPORTS}
    for name in schemacast._EXPORTS:
        assert getattr(schemacast, name) is not None
