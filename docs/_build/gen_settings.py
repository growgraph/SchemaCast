"""Generate ``reference/settings.md`` from the settings models at build time.

One table per settings class, one row per field: the environment variable, its
default and its description. Generated, never written to disk, so it cannot
drift from ``schemacast/config.py``.
"""

from typing import Any

import mkdocs_gen_files
from pydantic_settings import BaseSettings

from schemacast.config import CacheSettings, LLMSettings, SchemaCastSettings, ServeSettings

SECTIONS: list[tuple[str, type[BaseSettings], str]] = [
    ("Model", LLMSettings, "SCHEMACAST_LLM_"),
    ("Response cache", CacheSettings, "SCHEMACAST_CACHE_"),
    ("Cast", SchemaCastSettings, "SCHEMACAST_"),
    ("Service", ServeSettings, "SCHEMACAST_SERVE_"),
]


def _cell(text: str) -> str:
    return " ".join(text.replace("``", "`").split()).replace("|", "\\|")


def _default(value: Any) -> str:
    if value is None:
        return "unset"
    if isinstance(value, bool):
        return f"`{str(value).lower()}`"
    return f"`{value}`"


lines = [
    "# Settings",
    "",
    "Settings are read from environment variables only; no file is loaded. In",
    "Python, pass `SchemaCastSettings(...)` to `SchemaCaster` instead.",
    "",
]
for title, model, prefix in SECTIONS:
    lines += [f"## {title}", "", "| Variable | Default | Meaning |", "|---|---|---|"]
    for name, field in model.model_fields.items():
        if isinstance(field.default_factory, type):
            continue  # nested settings: their own section
        lines.append(
            f"| `{prefix}{name.upper()}` | {_default(field.default)} | "
            f"{_cell(field.description or '')} |"
        )
    lines.append("")

with mkdocs_gen_files.open("reference/settings.md", "w") as f:
    f.write("\n".join(lines))
