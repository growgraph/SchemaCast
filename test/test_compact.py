"""SchemaCast takes a schema in graflo's compact notation wherever it takes one,
and converts both ways; the notation itself is tested in graflo."""

from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from schemacast.cli import main
from schemacast.schema_io import load_schema

EXAMPLES = Path(__file__).parents[1] / "examples"

# The tickets example's schema, in the compact notation.
TICKETS = """
name: support
version: 1.0.0
types:
  Customer:
    about: A customer account.
    aka: [client]
    key: account
    alt_keys: [email]
    fields: {account: str, email: str, name: str}
  Agent:
    about: A support agent.
    key: handle
    fields: {handle: str, name: str}
  Ticket:
    about: A support ticket.
    aka: [case, issue]
    key: number
    fields:
      number: str
      title: {type: str, about: A one-line summary of the problem.}
      severity: int
      opened: datetime
      wait:
        type: float
        about: Hours the customer waited for a fix.
        aka: [waited, waiting time]
        unit: h
  Product:
    about: A product the company sells.
    key: sku
    fields: {sku: str, name: str}
  Tag:
    about: A label put on tickets.
    key: label
    hashed: true
    fields: {label: str}
  Comment:
    about: A remark written on a ticket.
    fields: {body: str}
relations:
  - Customer RAISED Ticket
  - {from: Agent, name: HANDLES, to: Ticket, fields: {since: datetime}}
  - Ticket ABOUT Product
  - Ticket DUPLICATE_OF Ticket
  - Ticket TAGGED Tag
  - Comment ON Ticket
"""


@pytest.fixture
def tickets():
    return load_schema(EXAMPLES / "tickets" / "schema.yaml")


def test_a_compact_schema_loads_from_a_file_like_any_other(tmp_path, tickets):
    path = tmp_path / "schema.yaml"
    path.write_text(TICKETS, encoding="utf-8")
    loaded = load_schema(path)
    assert loaded.to_minimal_canonical_dict() == tickets.to_minimal_canonical_dict()


def _graflo(**vertex) -> dict:
    return {
        "metadata": {"name": "s"},
        "core_schema": {
            "vertex_config": {
                "vertices": [
                    {"name": "A", "properties": [], "identity": ["k"], **vertex}
                ]
            },
            "edge_config": {"edges": []},
        },
    }


def test_the_cli_prints_either_form(tmp_path, tickets):
    path = tmp_path / "compact.yaml"
    path.write_text(TICKETS, encoding="utf-8")
    runner = CliRunner()
    graflo = runner.invoke(main, ["schema", "--schema", str(path)])
    assert graflo.exit_code == 0, graflo.output
    assert yaml.safe_load(graflo.output) == tickets.to_minimal_canonical_dict()
    compact = runner.invoke(
        main,
        [
            "schema",
            "--schema",
            str(EXAMPLES / "tickets" / "schema.yaml"),
            "--to",
            "compact",
        ],
    )
    assert compact.exit_code == 0, compact.output
    assert yaml.safe_load(compact.output) == yaml.safe_load(TICKETS)


def test_the_cli_refuses_a_lossy_conversion(tmp_path):
    path = tmp_path / "schema.yaml"
    path.write_text(
        yaml.safe_dump(_graflo(properties=[{"name": "k", "type": "UINT"}])),
        encoding="utf-8",
    )
    result = CliRunner().invoke(
        main, ["schema", "--schema", str(path), "--to", "compact"]
    )
    assert result.exit_code == 1
    assert "cannot express" in result.output


def test_a_bad_schema_file_is_one_clean_message(tmp_path):
    path = tmp_path / "schema.yaml"
    path.write_text(
        "name: x\ntypes:\n  A: {key: k, fields: {k: string}}\n", encoding="utf-8"
    )
    result = CliRunner().invoke(main, ["sheet", "--schema", str(path)])
    assert result.exit_code == 1
    assert "not a valid schema" in result.output
    assert "types.A.fields.k.type" in result.output
    assert "Traceback" not in result.output


def test_cast_reads_a_compact_schema_and_it_owns_nothing(tmp_path, tickets):
    from schemacast.schema_io import load_contract

    path = tmp_path / "schema.yaml"
    path.write_text(TICKETS, encoding="utf-8")
    schema, owned = load_contract(path)
    assert schema.to_minimal_canonical_dict() == tickets.to_minimal_canonical_dict()
    assert owned == frozenset()
