"""A cast on disk.

| File | Holds | Same bytes on replay |
|---|---|---|
| ``graph.json`` | the schema and the graph (graflo ``GraFloOutput``) | yes |
| ``groundings.jsonl`` | one grounding per vertex, edge and property | yes |
| ``pending.jsonl`` | edges awaiting a secondary-identity lookup | yes |
| ``episodes.jsonl`` | one result per episode | yes |
| ``quarantine.jsonl`` | what was left out, and ``unmapped`` items | yes |
| ``unresolved.jsonl`` | mentions with no key and no picked entity, never minted | yes |
| ``manifest.json`` | versions, settings, counts and usage | no: usage differs |
| ``write.json`` | what was sent to the database, when one was given | — |

``manifest.json`` names the ``output_format_version``. Version 2: a grounding
whose ``in_context`` is set has offsets into that context item's text.
Version 3: a vertex grounding names how the vertex was identified
(``resolved_by``), and unresolved mentions have their own file.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from schemacast.cast import CastResult

QUARANTINE_FILE = "quarantine.jsonl"
UNRESOLVED_FILE = "unresolved.jsonl"


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, indent=1)


def _jsonl(models: Iterable[BaseModel]) -> str:
    return "".join(
        json.dumps(
            model.model_dump(mode="json", exclude_none=True),
            ensure_ascii=False,
            sort_keys=True,
        )
        + "\n"
        for model in models
    )


def save_cast(
    result: CastResult, out_dir: str | Path, *, write_report: BaseModel | None = None
) -> Path:
    """Write *result* under *out_dir* (created if needed); returns the directory."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "graph.json").write_text(
        _json(result.output.to_dict()) + "\n", encoding="utf-8"
    )
    (out / "groundings.jsonl").write_text(_jsonl(result.groundings), encoding="utf-8")
    (out / "pending.jsonl").write_text(_jsonl(result.pending), encoding="utf-8")
    (out / "episodes.jsonl").write_text(_jsonl(result.episodes), encoding="utf-8")
    (out / UNRESOLVED_FILE).write_text(_jsonl(result.unresolved), encoding="utf-8")
    (out / "manifest.json").write_text(
        _json(result.manifest.model_dump(mode="json")) + "\n", encoding="utf-8"
    )
    if write_report is not None:
        (out / "write.json").write_text(
            _json(write_report.model_dump(mode="json")) + "\n", encoding="utf-8"
        )
    return out
