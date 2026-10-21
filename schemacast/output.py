"""A cast on disk.

| File | Holds | Same bytes on replay |
|---|---|---|
| ``mentions.jsonl`` | one graflo ``RecordGraph`` per episode: what each text names | yes |
| ``graph.json`` | the schema and what the records' keys identify (graflo ``GraFloOutput``) | yes |
| ``staged.jsonl`` | records not identified, with why, and the relations they hold | yes |
| ``groundings.jsonl`` | one grounding per record, relation and property | yes |
| ``episodes.jsonl`` | one result per episode | yes |
| ``quarantine.jsonl`` | what was left out, and ``unmapped`` items | yes |
| ``manifest.json`` | versions, settings, counts and usage | no: usage differs |
| ``write.json`` | what was sent to the database, when one was given | — |

A pick (``save_picks``) writes ``picks.jsonl`` (one pick per staged record),
``episodes.jsonl`` and ``manifest.json`` to its own directory.

With a database, ``staged.jsonl`` holds what is still staged after the lookups
there. ``manifest.json`` names the ``output_format_version``. Version 5: a cast
is a record graph, quotes and spans in ``evidence``; ``unmapped`` is outside it.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from schemacast.cast import CastResult

if TYPE_CHECKING:
    from graflo.link.records import Resolution

    from schemacast.pick import PickResult

QUARANTINE_FILE = "quarantine.jsonl"
STAGED_FILE = "staged.jsonl"


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, indent=1)


def _line(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True) + "\n"


def _jsonl(models: Iterable[BaseModel]) -> str:
    return "".join(
        _line(model.model_dump(mode="json", exclude_none=True)) for model in models
    )


def staged_lines(resolution: Resolution) -> str:
    """Staged records (a ``record`` key), then held relations (a ``relation``
    key), one JSON object per line."""
    staged = resolution.staged
    return _jsonl([*staged.records, *staged.relations])


def save_cast(
    result: CastResult,
    out_dir: str | Path,
    *,
    write_report: BaseModel | None = None,
    resolution: Resolution | None = None,
) -> Path:
    """Write *result* under *out_dir* (created if needed); returns the directory.

    *resolution*, from a database write, replaces the offline one in
    ``staged.jsonl``.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "mentions.jsonl").write_text(_jsonl(result.graphs), encoding="utf-8")
    (out / "graph.json").write_text(
        _json(result.output.to_dict()) + "\n", encoding="utf-8"
    )
    (out / STAGED_FILE).write_text(
        staged_lines(resolution or result.resolution), encoding="utf-8"
    )
    (out / "groundings.jsonl").write_text(_jsonl(result.groundings), encoding="utf-8")
    (out / "episodes.jsonl").write_text(_jsonl(result.episodes), encoding="utf-8")
    (out / "manifest.json").write_text(
        _json(result.manifest.model_dump(mode="json")) + "\n", encoding="utf-8"
    )
    if write_report is not None:
        (out / "write.json").write_text(
            _json(write_report.model_dump(mode="json")) + "\n", encoding="utf-8"
        )
    return out


def save_picks(result: PickResult, out_dir: str | Path) -> Path:
    """Write a pick run under *out_dir* (created if needed); returns the directory."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "picks.jsonl").write_text(_jsonl(result.picks), encoding="utf-8")
    (out / "episodes.jsonl").write_text(_jsonl(result.episodes), encoding="utf-8")
    (out / "manifest.json").write_text(
        _json(result.manifest.model_dump(mode="json")) + "\n", encoding="utf-8"
    )
    return out
