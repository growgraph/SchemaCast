"""The benchmark corpus: texts, the schema they are cast against, and the graph
each text states.

A case is data, never code: ``cases/<family>/<instance>/`` holds ``case.yaml``,
``episodes.jsonl`` and ``expected.yaml``, and optionally ``pick.yaml``: for an
episode's staged mentions, the candidates a resolver would gather, the entities
the record names and the pick a careful reader makes. ``case.yaml`` names its
schema from ``schemas/``, either one file or a list whose vertices and edges
are merged.
A family isolates one failure mode; its instances are that failure mode in
different domains.

Beside the graph a text states, ``expected.yaml`` may give ``forbidden``
items a cast must not write and ``allowed`` items it may write without being
required to; an outside corpus in the same layout loads with
:func:`corpus_dirs`.

The expected graph is exact: every quote it gives occurs in the text, and
every value is what the schema's field makes of it. ``test_bench`` checks both,
and that an oracle replying with the expected graph scores perfectly.

Expected graphs are an interface: changing a case invalidates every recorded
result, so :data:`CORPUS_VERSION` moves with it and :data:`CORPUS_DIGEST` pins
the two together.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Literal

import yaml
from graflo.architecture.schema.document import Schema
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from schemacast.episode import Episode, read_episodes

BENCH_DIR = Path(__file__).parent
CASES_DIR = BENCH_DIR / "cases"
SCHEMAS_DIR = BENCH_DIR / "schemas"
SPLITS_FILE = CASES_DIR / "SPLITS.yaml"

#: Bump whenever a case, schema or split assignment changes. Results from
#: different versions are not comparable.
CORPUS_VERSION = "1.3.0"

#: Content hash of ``cases/`` and ``schemas/`` at :data:`CORPUS_VERSION`.
CORPUS_DIGEST = "6a293a0455ccd4ea0dc59fe0583fe0a53db232a710949f83b48e2dd2fbdd7e97"

Split = Literal["dev", "test"]


class ExpectedProp(BaseModel):
    """A property value as the schema's field holds it, and the text it is read from.

    ``quote`` defaults to the value as written; give it when the text writes the
    value differently (``200 kW`` for ``200.0``, ``4 March 2024`` for a date).
    """

    model_config = ConfigDict(extra="forbid")

    value: Any
    quote: str | None = None
    context: str | None = Field(
        default=None,
        description="Source id of the context item the quote is in; only an "
        "identity value may be read from context.",
    )

    @property
    def evidence(self) -> str:
        """The quote, or the value as written."""
        if self.quote is not None:
            return self.quote
        return str(self.value)


def _props(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            name: item if isinstance(item, dict) else {"value": item}
            for name, item in value.items()
        }
    return value


class ExpectedVertex(BaseModel):
    """An entity the text states or refers to."""

    model_config = ConfigDict(extra="forbid")

    type: str
    quote: str = Field(description="Where the text names the entity.")
    mention: bool = Field(
        default=False,
        description=(
            "The text only refers to it: written by its key, or looked up by a "
            "secondary identity."
        ),
    )
    props: dict[str, ExpectedProp] = Field(default_factory=dict)

    @field_validator("props", mode="before")
    @classmethod
    def _wrap_scalars(cls, value: Any) -> Any:
        return _props(value)


class ExpectedEdge(BaseModel):
    """A relation the text states, between two vertex handles."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    relation: str
    from_: str = Field(alias="from")
    to: str
    quote: str
    props: dict[str, ExpectedProp] = Field(default_factory=dict)

    @field_validator("props", mode="before")
    @classmethod
    def _wrap_scalars(cls, value: Any) -> Any:
        return _props(value)


class ExpectedGraph(BaseModel):
    """Vertices keyed by a handle local to the episode, and edges between handles."""

    model_config = ConfigDict(extra="forbid")

    vertices: dict[str, ExpectedVertex] = Field(default_factory=dict)
    edges: list[ExpectedEdge] = Field(default_factory=list)


class ExpectedEpisode(ExpectedGraph):
    """What one episode states, what a cast of it must not write, and what it
    may write.

    ``forbidden`` holds items an obedient or careless model would produce: what
    an injected instruction asks for, or an entity the text gives no identity.
    ``allowed`` holds items a cast may produce without being required to: an
    entity or relation the text implies but does not state. A produced item
    matching one counts neither as right nor as wrong. The edges of either may
    use handles of the expected graph; their own handles must differ from its.
    """

    forbidden: ExpectedGraph = Field(default_factory=ExpectedGraph)
    allowed: ExpectedGraph = Field(default_factory=ExpectedGraph)

    @model_validator(mode="after")
    def _distinct_handles(self) -> ExpectedEpisode:
        shared = set(self.vertices) & set(self.allowed.vertices)
        if shared:
            raise ValueError(f"allowed reuses expected handles {sorted(shared)}")
        return self


class ExpectedCandidate(BaseModel):
    """An entity offered to a pick, as graflo's ``EntityCandidate`` holds it.

    ``anchor`` names an expected vertex handle of the same episode.
    """

    model_config = ConfigDict(extra="forbid")

    type: str
    key: dict[str, Any]
    label: str | None = None
    lane: str = "metadata"
    matched: str | None = None
    relation: str | None = None
    anchor: str | None = None
    role: str | None = None


class ExpectedPickMention(BaseModel):
    """One staged mention's candidates and the right pick (null: none fits)."""

    model_config = ConfigDict(extra="forbid")

    candidates: list[ExpectedCandidate] = Field(default_factory=list)
    pick: dict[str, Any] | None = None


class ExpectedPicks(BaseModel):
    """``pick.yaml`` for one episode: mentions by expected vertex handle."""

    model_config = ConfigDict(extra="forbid")

    entities: list[ExpectedCandidate] = Field(
        default_factory=list, description="Entities the record's metadata names."
    )
    mentions: dict[str, ExpectedPickMention] = Field(default_factory=dict)


class CaseSettings(BaseModel):
    """Settings a case pins, e.g. a slice budget tight enough to select."""

    model_config = ConfigDict(extra="forbid")

    slice_max_elements: int | None = None
    slice_max_tokens: int | None = None


class CaseMeta(BaseModel):
    """``case.yaml``."""

    model_config = ConfigDict(extra="forbid")

    description: str
    schema_: str | list[str] = Field(alias="schema")
    recast: bool = Field(
        default=False,
        description="Cast a second time with a fresh model call and compare.",
    )
    settings: CaseSettings = Field(default_factory=CaseSettings)


class BenchCase(BaseModel):
    """One case, loaded."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    family: str
    instance: str
    split: Split
    description: str
    schema_: Schema = Field(alias="schema")
    recast: bool
    settings: CaseSettings
    episodes: list[Episode]
    expected: dict[str, ExpectedEpisode]
    picks: dict[str, ExpectedPicks] = Field(default_factory=dict)
    digest: str = Field(
        description="SHA-256 of the case's files, its split and its schemas: "
        "two runs compare on a case only when this matches."
    )


def _read_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def load_schema_parts(
    names: str | list[str], schemas_dir: Path = SCHEMAS_DIR
) -> Schema:
    """One schema from ``schemas/<name>.yaml`` files.

    Vertices and edges are concatenated in order; metadata is the last file's.
    """
    parts = [names] if isinstance(names, str) else names
    vertices: list[Any] = []
    edges: list[Any] = []
    metadata: Any = None
    for name in parts:
        data = _read_yaml(schemas_dir / f"{name}.yaml")
        core = data["core_schema"]
        vertices += core["vertex_config"]["vertices"]
        edges += core["edge_config"]["edges"]
        metadata = data.get("metadata", metadata)
    return Schema.model_validate(
        {
            "metadata": metadata,
            "core_schema": {
                "vertex_config": {"vertices": vertices},
                "edge_config": {"edges": edges},
            },
        }
    )


def load_splits(path: Path = SPLITS_FILE) -> dict[str, Split]:
    """Case name → ``dev`` or ``test``."""
    raw = _read_yaml(path) or {}
    return {name: half for half in ("dev", "test") for name in raw.get(half) or []}


def load_case(
    directory: Path,
    *,
    cases_dir: Path = CASES_DIR,
    schemas_dir: Path = SCHEMAS_DIR,
    splits: dict[str, Split] | None = None,
) -> BenchCase:
    """Load one case directory.

    Raises:
        ValueError: the case has no split, or its expected graph and episodes
            name different source ids.
    """
    name = directory.relative_to(cases_dir).as_posix()
    family, _, instance = name.partition("/")
    assignment = load_splits(cases_dir / "SPLITS.yaml") if splits is None else splits
    if name not in assignment:
        raise ValueError(f"{name}: not assigned to dev or test in SPLITS.yaml")
    meta = CaseMeta.model_validate(_read_yaml(directory / "case.yaml"))
    episodes = list(read_episodes(directory / "episodes.jsonl"))
    raw = _read_yaml(directory / "expected.yaml") or {}
    expected = {
        source_id: ExpectedEpisode.model_validate(item or {})
        for source_id, item in raw.items()
    }
    ids = [episode.source_id for episode in episodes]
    if sorted(ids) != sorted(expected) or len(set(ids)) != len(ids):
        raise ValueError(
            f"{name}: episodes {sorted(ids)} and expected {sorted(expected)} differ"
        )
    pick_file = directory / "pick.yaml"
    picks = {
        source_id: ExpectedPicks.model_validate(item or {})
        for source_id, item in (
            (_read_yaml(pick_file) or {}) if pick_file.exists() else {}
        ).items()
    }
    for source_id, item in picks.items():
        vertices = expected[source_id].vertices if source_id in expected else None
        if vertices is None or not set(item.mentions) <= set(vertices):
            raise ValueError(f"{name}: pick.yaml names an unknown episode or handle")
    parts = [meta.schema_] if isinstance(meta.schema_, str) else meta.schema_
    digest = hashlib.sha256(f"{name}\0{assignment[name]}".encode())
    for path in [
        directory / "case.yaml",
        directory / "episodes.jsonl",
        directory / "expected.yaml",
        *([pick_file] if pick_file.exists() else []),
        *(schemas_dir / f"{part}.yaml" for part in parts),
    ]:
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return BenchCase(
        name=name,
        family=family,
        instance=instance or family,
        split=assignment[name],
        description=meta.description,
        schema=load_schema_parts(meta.schema_, schemas_dir),
        recast=meta.recast,
        settings=meta.settings,
        episodes=episodes,
        expected=expected,
        picks=picks,
        digest=digest.hexdigest(),
    )


def load_corpus(
    cases_dir: Path = CASES_DIR, schemas_dir: Path = SCHEMAS_DIR
) -> list[BenchCase]:
    """Every case, sorted by name."""
    splits = load_splits(cases_dir / "SPLITS.yaml")
    return [
        load_case(
            path.parent, cases_dir=cases_dir, schemas_dir=schemas_dir, splits=splits
        )
        for path in sorted(cases_dir.rglob("case.yaml"))
    ]


def corpus_dirs(root: Path) -> tuple[Path, Path]:
    """``root/cases`` and ``root/schemas``: a corpus laid out like the bundled one.

    Raises:
        ValueError: *root* has no ``cases/SPLITS.yaml`` or no ``schemas/``.
    """
    cases, schemas = root / "cases", root / "schemas"
    if not (cases / "SPLITS.yaml").is_file() or not schemas.is_dir():
        raise ValueError(f"{root}: expected cases/SPLITS.yaml and schemas/")
    return cases, schemas


def corpus_digest(bench_dir: Path = BENCH_DIR) -> str:
    """SHA-256 over every file of ``cases/`` and ``schemas/``, paths included."""
    digest = hashlib.sha256()
    for sub in ("cases", "schemas"):
        for path in sorted((bench_dir / sub).rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                digest.update(path.relative_to(bench_dir).as_posix().encode("utf-8"))
                digest.update(path.read_bytes())
    return digest.hexdigest()
