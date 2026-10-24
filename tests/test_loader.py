"""Tests for model validation and the input loader."""

import json
import shutil
from pathlib import Path

import pytest

from solmu.core.loader import (
    LoadError,
    load_commodities,
    load_network,
    load_nodes,
    load_policy,
    load_scenario,
)
from solmu.core.model import DemandEvent, EdgeCapacityEvent, NodeCapacityEvent

FIXTURES = Path(__file__).parent / "fixtures" / "tiny"


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    target = tmp_path / "net"
    shutil.copytree(FIXTURES, target)
    return target


def _replace(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new), encoding="utf-8")


def test_valid_fixture_loads() -> None:
    net = load_network(FIXTURES)
    assert [n.id for n in net.nodes] == ["src-a", "hub-a", "reg-a", "reg-b"]
    assert len(net.edges) == 3
    assert len(net.demand) == 3
    assert len(net.supply) == 2
    assert net.node("reg-a").population == 1_000_000
    assert net.node("src-a").population is None
    assert net.edge("rail-hub-a").transit_days == 2


def test_commodities_policy_scenario_load() -> None:
    commodities = load_commodities(FIXTURES / "commodities.json")
    assert [c.id for c in commodities.by_priority()] == ["pills", "grain"]
    policy = load_policy(FIXTURES / "policy.json")
    assert policy.release.trigger == "shortage"
    assert policy.release.delay_days == 2
    scenario = load_scenario(FIXTURES / "scenario.json")
    kinds = [type(e) for e in scenario.events]
    assert kinds == [NodeCapacityEvent, EdgeCapacityEvent, DemandEvent]


def test_tags_parse() -> None:
    nodes = {n.id: n for n in load_nodes(FIXTURES / "nodes.csv")}
    assert nodes["hub-a"].tags == {"zone": "north", "role": "transfer"}
    assert nodes["src-a"].tags == {}
    assert nodes["reg-b"].tags == {"zone": "south", "kind": "test"}


def test_bidirectional_expansion() -> None:
    arcs = {a.id: a for a in load_network(FIXTURES).arcs()}
    assert set(arcs) == {
        "road-src-hub",
        "rail-hub-a",
        "rail-hub-a:rev",
        "road-hub-b",
        "road-hub-b:rev",
    }
    fwd, rev = arcs["rail-hub-a"], arcs["rail-hub-a:rev"]
    assert (fwd.from_node, fwd.to_node) == ("hub-a", "reg-a")
    assert (rev.from_node, rev.to_node) == ("reg-a", "hub-a")
    assert rev.edge_id == "rail-hub-a"


def test_edge_with_missing_node_fails_naming_edge_and_file(data_dir: Path) -> None:
    _replace(data_dir / "edges.csv", "hub-a,reg-b", "hub-a,reg-zzz")
    with pytest.raises(LoadError) as exc:
        load_network(data_dir)
    msg = str(exc.value)
    assert "road-hub-b" in msg
    assert "edges.csv" in msg
    assert "reg-zzz" in msg


def test_negative_capacity_fails(data_dir: Path) -> None:
    _replace(data_dir / "edges.csv", "road,100,1", "road,-5,1")
    with pytest.raises(LoadError) as exc:
        load_network(data_dir)
    msg = str(exc.value)
    assert "edges.csv" in msg
    assert "road-src-hub" in msg
    assert "capacity_per_day" in msg


def test_zero_transit_days_fails(data_dir: Path) -> None:
    _replace(data_dir / "edges.csv", "road,100,1", "road,100,0")
    with pytest.raises(LoadError, match="transit_days"):
        load_network(data_dir)


def test_duplicate_node_id_rejected(data_dir: Path) -> None:
    with (data_dir / "nodes.csv").open("a", encoding="utf-8") as fh:
        fh.write("reg-a,Again,region,2.0,2.0,10,XX,\n")
    with pytest.raises(LoadError, match="duplicate node id 'reg-a'"):
        load_network(data_dir)


def test_demand_unknown_node_fails(data_dir: Path) -> None:
    _replace(data_dir / "demand.csv", "reg-b,pills", "reg-q,pills")
    with pytest.raises(LoadError, match=r"demand.csv: row 4.*reg-q"):
        load_network(data_dir)


def test_malformed_tags_fail(data_dir: Path) -> None:
    _replace(data_dir / "nodes.csv", "zone=north\n", "justakey\n")
    with pytest.raises(LoadError, match="tags"):
        load_network(data_dir)


def test_scenario_until_before_day_fails(tmp_path: Path) -> None:
    data = json.loads((FIXTURES / "scenario.json").read_text(encoding="utf-8"))
    data["events"][0]["until_day"] = 0
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(LoadError, match="bad.json"):
        load_scenario(path)
