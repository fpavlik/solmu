"""Load and validate CSV/JSON input files into typed model objects."""

from __future__ import annotations

import csv
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from solmu.core.model import (
    Commodities,
    DemandRecord,
    Edge,
    Network,
    Node,
    Policy,
    Scenario,
    SupplyRecord,
)

M = TypeVar("M", bound=BaseModel)

NODES_FILE = "nodes.csv"
EDGES_FILE = "edges.csv"
DEMAND_FILE = "demand.csv"
SUPPLY_FILE = "supply.csv"


class LoadError(ValueError):
    """Invalid input data; the message names file, record and field."""


def _format_errors(file: Path, record: str, exc: ValidationError) -> LoadError:
    lines = []
    for err in exc.errors():
        field = ".".join(str(p) for p in err["loc"]) or "<record>"
        lines.append(f"{file.name}: {record}, field {field!r}: {err['msg']}")
    return LoadError("\n".join(lines))


def _validate(model: type[M], data: dict[str, Any], file: Path, record: str) -> M:
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise _format_errors(file, record, exc) from exc


def _parse_tags(raw: str, file: Path, record: str) -> dict[str, str]:
    tags: dict[str, str] = {}
    for part in filter(None, (p.strip() for p in raw.split(";"))):
        key, sep, value = part.partition("=")
        if not sep or not key.strip():
            raise LoadError(
                f"{file.name}: {record}, field 'tags': expected key=value, got {part!r}"
            )
        tags[key.strip()] = value.strip()
    return tags


def _read_csv(
    path: Path,
    model: type[M],
    prepare: Callable[[dict[str, str], Path, str], dict[str, Any]] | None = None,
) -> list[M]:
    path = Path(path)
    if not path.is_file():
        raise LoadError(f"{path.name}: file not found at {path}")
    records: list[M] = []
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            label = f"row {reader.line_num}"
            if row.get("id"):
                label += f" (id {row['id']!r})"
            if None in row:
                raise LoadError(f"{path.name}: {label}: more values than header columns")
            data: dict[str, Any] = dict(row)
            if prepare is not None:
                data = prepare(row, path, label)
            records.append(_validate(model, data, path, label))
    return records


def _prepare_node(row: dict[str, str], file: Path, record: str) -> dict[str, Any]:
    data: dict[str, Any] = dict(row)
    data["tags"] = _parse_tags(row.get("tags") or "", file, record)
    if not (row.get("population") or "").strip():
        data["population"] = None
    return data


def _prepare_edge(row: dict[str, str], file: Path, record: str) -> dict[str, Any]:
    data: dict[str, Any] = dict(row)
    ids = [p.strip() for p in (row.get("commodities") or "").split("|") if p.strip()]
    if ids:
        data["commodities"] = frozenset(ids)
    else:
        data.pop("commodities", None)
    return data


def _prepare_supply(row: dict[str, str], file: Path, record: str) -> dict[str, Any]:
    data: dict[str, Any] = dict(row)
    if not (row.get("max_tonnes_per_day") or "").strip():
        data.pop("max_tonnes_per_day", None)
    return data


def _read_json(path: Path, model: type[M]) -> M:
    path = Path(path)
    if not path.is_file():
        raise LoadError(f"{path.name}: file not found at {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise LoadError(f"{path.name}: invalid JSON at line {exc.lineno}: {exc.msg}") from exc
    record = f"id {raw['id']!r}" if isinstance(raw, dict) and "id" in raw else "document"
    return _validate(model, raw, path, record)


def load_commodities(path: Path) -> Commodities:
    """Load `commodities.json`."""
    return _read_json(path, Commodities)


def load_nodes(path: Path) -> list[Node]:
    """Load `nodes.csv`; `tags` is `key=value;key=value`."""
    return _read_csv(path, Node, _prepare_node)


def load_edges(path: Path) -> list[Edge]:
    """Load `edges.csv`."""
    return _read_csv(path, Edge, _prepare_edge)


def load_demand(path: Path) -> list[DemandRecord]:
    """Load `demand.csv`."""
    return _read_csv(path, DemandRecord)


def load_supply(path: Path) -> list[SupplyRecord]:
    """Load `supply.csv`."""
    return _read_csv(path, SupplyRecord, _prepare_supply)


def load_policy(path: Path) -> Policy:
    """Load a policy profile JSON."""
    return _read_json(path, Policy)


def load_scenario(path: Path) -> Scenario:
    """Load a scenario JSON."""
    return _read_json(path, Scenario)


def _check_unique(ids: list[str], file: str, what: str) -> None:
    seen: set[str] = set()
    for i in ids:
        if i in seen:
            raise LoadError(f"{file}: duplicate {what} id {i!r}")
        seen.add(i)


def _check_records(
    records: list[DemandRecord] | list[SupplyRecord], file: str, node_ids: set[str]
) -> None:
    seen: set[tuple[str, str]] = set()
    for n, rec in enumerate(records, start=2):
        if rec.node_id not in node_ids:
            raise LoadError(
                f"{file}: row {n}, field 'node_id': unknown node {rec.node_id!r} "
                f"(not in {NODES_FILE})"
            )
        key = (rec.node_id, rec.commodity_id)
        if key in seen:
            raise LoadError(
                f"{file}: row {n}: duplicate record for node {key[0]!r}, commodity {key[1]!r}"
            )
        seen.add(key)


def load_network(data_dir: Path) -> Network:
    """Load nodes, edges, demand and supply from a directory and cross-validate them."""
    data_dir = Path(data_dir)
    nodes = load_nodes(data_dir / NODES_FILE)
    edges = load_edges(data_dir / EDGES_FILE)
    demand = load_demand(data_dir / DEMAND_FILE)
    supply = load_supply(data_dir / SUPPLY_FILE)

    _check_unique([n.id for n in nodes], NODES_FILE, "node")
    _check_unique([e.id for e in edges], EDGES_FILE, "edge")
    node_ids = {n.id for n in nodes}
    for e in edges:
        for field, ref in (("from_node", e.from_node), ("to_node", e.to_node)):
            if ref not in node_ids:
                raise LoadError(
                    f"{EDGES_FILE}: edge {e.id!r}, field {field!r}: unknown node {ref!r} "
                    f"(not in {NODES_FILE})"
                )
    _check_records(demand, DEMAND_FILE, node_ids)
    _check_records(supply, SUPPLY_FILE, node_ids)
    return Network(nodes=nodes, edges=edges, demand=demand, supply=supply)
