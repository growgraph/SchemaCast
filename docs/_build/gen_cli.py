"""Generate ``reference/cli.md`` from the click commands at build time.

One section per command, its help and a table of its options, read from the
command objects themselves, so the page cannot drift from ``schemacast/cli.py``.
"""

import click
import mkdocs_gen_files

from schemacast.bench.cli import main as bench
from schemacast.cli import main as schemacast


def _cell(text: str) -> str:
    return " ".join(text.split()).replace("|", "\\|")


def _option(param: click.Parameter) -> list[str]:
    if isinstance(param, click.Argument):
        return [f"`{param.name.upper()}`" if param.name else "", "argument", ""]
    assert isinstance(param, click.Option)
    names = " / ".join(f"`{name}`" for name in [*param.opts, *param.secondary_opts])
    kind = "flag" if param.is_flag else param.type.name.upper()
    if isinstance(param.type, click.Choice):
        kind = " \\| ".join(f"`{choice}`" for choice in param.type.choices)
    notes = []
    if param.required:
        notes.append("required")
    if param.multiple:
        notes.append("repeatable")
    if param.show_default and param.default not in (None, False, ()):
        notes.append(f"default `{param.default}`")
    help_text = _cell(param.help or "")
    if notes:
        help_text = f"{help_text} ({', '.join(notes)})".strip()
    return [names, kind, help_text]


def _section(command: click.Command, invocation: str) -> list[str]:
    lines = [f"## `{invocation}`", "", _cell(command.help or ""), ""]
    params = [p for p in command.params if p.name != "help"]
    if params:
        lines += ["| Option | Value | Meaning |", "|---|---|---|"]
        lines += ["| " + " | ".join(_option(p)) + " |" for p in params]
        lines.append("")
    return lines


lines = [
    "# Command line",
    "",
    "`schemacast` casts text; `schemacast-bench` runs the",
    "[benchmark](../guides/benchmark.md). Model settings come from the",
    "[environment](settings.md).",
    "",
]
assert isinstance(schemacast, click.Group)
for name, command in schemacast.commands.items():
    lines += _section(command, f"schemacast {name}")
assert isinstance(bench, click.Group)
for name, command in bench.commands.items():
    invocation = "schemacast-bench" if name == "run" else f"schemacast-bench {name}"
    lines += _section(command, invocation)

with mkdocs_gen_files.open("reference/cli.md", "w") as f:
    f.write("\n".join(lines))
