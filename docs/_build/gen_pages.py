"""Generate the Python API reference at build time.

Walks ``schemacast/**/*.py`` and writes one ``mkdocstrings`` page per public
module under ``reference/python/``, into the virtual docs directory that
``mkdocs-gen-files`` feeds the build. Private modules (``_*.py``) are skipped.

These pages are never written to disk and must not be committed: the plugin
opens each path in ``"w"`` mode, so a committed file at a generated path is
replaced before anything renders.

A hand-written overview at ``reference/python/<pkg>/index.md`` replaces the
generated ``reference/python/<pkg>.md`` (both build to the same URL), so a
package whose overview exists on disk is skipped here; it must carry
``::: schemacast.<pkg>`` itself. The nav is inferred by ``literate-nav``.
"""

from pathlib import Path

import mkdocs_gen_files

PACKAGE = "schemacast"
REFERENCE = Path("docs/reference/python")


def _is_public(path: Path) -> bool:
    rel = path.relative_to(PACKAGE)
    return not any(part.startswith("_") and part != "__init__.py" for part in rel.parts)


for path in sorted(Path(PACKAGE).rglob("*.py")):
    if not _is_public(path):
        continue
    is_pkg_init = path.name == "__init__.py"
    if is_pkg_init:
        if path.parent == Path(PACKAGE):
            continue
        pkg_dir = path.parent.relative_to(PACKAGE)
        if (REFERENCE / pkg_dir / "index.md").exists():
            continue
        doc_path = pkg_dir.with_suffix(".md")
    else:
        doc_path = path.relative_to(PACKAGE).with_suffix(".md")
    full_doc_path = Path("reference", "python", doc_path)

    parts = list(doc_path.with_suffix("").parts)
    if not parts:
        continue

    with mkdocs_gen_files.open(full_doc_path, "w") as f:
        ident = ".".join([PACKAGE, *parts])
        # The sidebar's nesting supplies the package path; the heading, and
        # with it the search result, keeps the full one.
        f.write(f'---\ntitle: "{parts[-1]}"\n---\n\n')
        if is_pkg_init:
            f.write(
                f"# `{ident}`\n\n"
                f"::: {ident}\n"
                f"    options:\n"
                f"      show_submodules: false\n"
                f"      summary:\n"
                f"        modules: true\n"
            )
        else:
            f.write(f"# `{ident}`\n\n::: {ident}\n")

    # Edit paths resolve against the docs directory; the module lives above it.
    mkdocs_gen_files.set_edit_path(full_doc_path, Path("..", path))
