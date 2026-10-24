"""Loader error paths and model validators."""

import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

from solmu.core.engine import Simulation
from solmu.core.loader import (
    LoadError,
    load_commodities,
    load_demand,
    load_edges,
    load_network,
    load_nodes,
    load_policy,
    load_scenario,
    load_supply,
)
from solmu.core.model import (
    Commodities,
    Commodity,
    Edge,
    Network,
    Node,
    ReleaseRule,
    Scenario,
    SupplyRecord,
)
from tests.helpers import manual_policy

HUBS = Path(__file__).parent / "fixtures" / "hubs"
TINY = Path(__file__).parent / "fixtures" / "tiny"
EDGE_HEADER = "id,from_node,to_node,mode,capacity_per_day,transit_days,cost_per_tonne"


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_missing_files_are_named() -> None:
    for loader in (load_nodes, load_edges, load_demand, load_supply):
        with pytest.raises(LoadError, match=r"nope\.csv: file not found"):
            loader(Path("nowhere") / "nope.csv")
    for loader in (load_commodities, load_policy, load_scenario):
        with pytest.raises(LoadError, match=r"nope\.json: file not found"):
            loader(Path("nowhere") / "nope.json")


def test_network_directory_without_nodes_file(tmp_path: Path) -> None:
    with pytest.raises(LoadError, match="nodes.csv: file not found"):
        load_network(tmp_path)


def test_bad_json_names_the_line(tmp_path: Path) -> None:
    path = _write(tmp_path, "bad.json", '{\n  "id": "p",\n  oops\n}')
    with pytest.raises(LoadError, match=r"bad\.json: invalid JSON at line 3"):
        load_policy(path)


def test_json_that_is_not_an_object_is_reported_as_a_document(tmp_path: Path) -> None:
    path = _write(tmp_path, "list.json", "[1, 2]")
    with pytest.raises(LoadError, match=r"list\.json: document"):
        load_scenario(path)


def test_json_unknown_field_and_bad_value_name_record_and_field(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "commodities.json",
        '{"id": "c", "commodities": [{"id": "a", "name": "A", "priority": 0, "colour": "red"}]}',
    )
    with pytest.raises(LoadError) as exc:
        load_commodities(path)
    text = str(exc.value)
    assert "commodities.json" in text and "id 'c'" in text
    assert "commodities.0.priority" in text and "commodities.0.colour" in text


def test_csv_row_with_extra_values(tmp_path: Path) -> None:
    path = _write(tmp_path, "demand.csv", "node_id,commodity_id,tonnes_per_day\nr,x,1,oops\n")
    with pytest.raises(LoadError, match="more values than header columns"):
        load_demand(path)


def test_csv_missing_header_column_names_the_field(tmp_path: Path) -> None:
    path = _write(tmp_path, "nodes.csv", "id,name,kind,lat\nn,N,region,1.0\n")
    with pytest.raises(LoadError, match=r"nodes\.csv: row 2 \(id 'n'\), field 'lon'"):
        load_nodes(path)


def test_unknown_node_kind(tmp_path: Path) -> None:
    path = _write(tmp_path, "nodes.csv", "id,name,kind,lat,lon\nn,N,depot,1.0,1.0\n")
    with pytest.raises(LoadError, match="field 'kind'"):
        load_nodes(path)


def test_latitude_out_of_range(tmp_path: Path) -> None:
    path = _write(tmp_path, "nodes.csv", "id,name,kind,lat,lon\nn,N,region,91,1.0\n")
    with pytest.raises(LoadError, match="field 'lat'"):
        load_nodes(path)


@pytest.mark.parametrize("value", ["maybe", "2", "yes please"])
def test_bad_boolean_in_edges(tmp_path: Path, value: str) -> None:
    path = _write(tmp_path, "edges.csv", f"{EDGE_HEADER},bidirectional\ne,a,b,road,1,1,1,{value}\n")
    with pytest.raises(LoadError, match="field 'bidirectional'"):
        load_edges(path)


@pytest.mark.parametrize("value", ["true", "false", "1", "0", "True"])
def test_valid_booleans_in_edges(tmp_path: Path, value: str) -> None:
    path = _write(tmp_path, "edges.csv", f"{EDGE_HEADER},bidirectional\ne,a,b,road,1,1,1,{value}\n")
    assert load_edges(path)[0].bidirectional is (value.lower() in {"true", "1"})


def test_unknown_edge_mode(tmp_path: Path) -> None:
    path = _write(tmp_path, "edges.csv", f"{EDGE_HEADER}\ne,a,b,air,1,1,1\n")
    with pytest.raises(LoadError, match="field 'mode'"):
        load_edges(path)


def test_edge_commodities_list_ignores_blank_parts_and_surrounding_space(tmp_path: Path) -> None:
    path = _write(
        tmp_path, "edges.csv", f"{EDGE_HEADER},commodities\ne,a,b,road,1,1,1,fuel| |food|\n"
    )
    assert load_edges(path)[0].commodities == {"fuel", "food"}


def test_tag_without_key_and_blank_population(tmp_path: Path) -> None:
    path = _write(
        tmp_path, "nodes.csv", "id,name,kind,lat,lon,population,tags\nn,N,region,1,1, ,=x\n"
    )
    with pytest.raises(LoadError, match="field 'tags'"):
        load_nodes(path)
    path = _write(
        tmp_path, "nodes.csv", "id,name,kind,lat,lon,population,tags\nn,N,region,1,1, ,a=1;;b = 2\n"
    )
    node = load_nodes(path)[0]
    assert node.population is None and node.tags == {"a": "1", "b": "2"}


def test_duplicate_demand_and_supply_rows_and_unknown_supply_node(tmp_path: Path) -> None:
    data = tmp_path / "net"
    shutil.copytree(TINY, data)
    with (data / "demand.csv").open("a", encoding="utf-8") as fh:
        fh.write("reg-a,grain,1\n")
    with pytest.raises(LoadError, match=r"demand\.csv: row 5: duplicate record"):
        load_network(data)

    shutil.rmtree(data)
    shutil.copytree(TINY, data)
    with (data / "supply.csv").open("a", encoding="utf-8") as fh:
        fh.write("nowhere,grain,1\n")
    with pytest.raises(LoadError, match=r"supply\.csv: row 4, field 'node_id': unknown node"):
        load_network(data)


def test_duplicate_edge_id(tmp_path: Path) -> None:
    data = tmp_path / "net"
    shutil.copytree(TINY, data)
    with (data / "edges.csv").open("a", encoding="utf-8") as fh:
        fh.write("road-src-hub,src-a,hub-a,road,1,1,1.0,false\n")
    with pytest.raises(LoadError, match="duplicate edge id 'road-src-hub'"):
        load_network(data)


def test_hub_fixture_fields_load() -> None:
    commodities = load_commodities(HUBS / "commodities.json")
    assert commodities.get("medicine").via_hubs
    assert commodities.import_redirect_delay_days == 2
    nodes = {n.id: n for n in load_nodes(HUBS / "nodes.csv")}
    assert nodes["h1"].tags == {"holds": "medicine|food"}


def test_holds_tag_with_unknown_commodity_is_rejected_by_the_engine() -> None:
    network = load_network(HUBS)
    nodes = [
        n.model_copy(update={"tags": {"holds": "medicine|gold"}}) if n.id == "h1" else n
        for n in network.nodes
    ]
    commodities = load_commodities(HUBS / "commodities.json")
    scenario = Scenario(id="s", name="S", horizon_days=2)
    with pytest.raises(ValueError, match=r"node 'h1', tag 'holds': unknown commodity id 'gold'"):
        Simulation(
            network.model_copy(update={"nodes": nodes}), scenario, manual_policy(), commodities
        )


def test_holds_tag_tolerates_blank_entries() -> None:
    network = load_network(HUBS)
    nodes = [
        n.model_copy(update={"tags": {"holds": " medicine | |food|"}}) if n.id == "h1" else n
        for n in network.nodes
    ]
    commodities = load_commodities(HUBS / "commodities.json")
    scenario = Scenario(id="s", name="S", horizon_days=2)
    sim = Simulation(
        network.model_copy(update={"nodes": nodes}), scenario, manual_policy(), commodities
    )
    assert sim.catchments["medicine"]["r1"] == "h1"


def test_unknown_commodity_ids_are_rejected_in_policy_demand_supply() -> None:
    commodities = load_commodities(HUBS / "commodities.json")
    scenario = Scenario(id="s", name="S", horizon_days=2)
    network = load_network(HUBS)
    with pytest.raises(ValueError, match="policy 'p'.*unknown commodity id 'gold'"):
        Simulation(
            network,
            scenario,
            manual_policy().model_copy(update={"min_stock_days": {"gold": 1.0}}),
            commodities,
        )
    bad_demand = network.demand[0].model_copy(update={"commodity_id": "gold"})
    with pytest.raises(ValueError, match="demand record for node 'r1'"):
        Simulation(
            network.model_copy(update={"demand": [bad_demand]}),
            scenario,
            manual_policy(),
            commodities,
        )
    bad_supply = network.supply[0].model_copy(update={"commodity_id": "gold"})
    with pytest.raises(ValueError, match="supply record for node 'p1'"):
        Simulation(
            network.model_copy(update={"supply": [bad_supply]}),
            scenario,
            manual_policy(),
            commodities,
        )


def test_duplicate_commodity_ids_rejected() -> None:
    with pytest.raises(ValidationError, match=r"duplicate commodity ids: \['a'\]"):
        Commodities(
            commodities=[
                Commodity(id="a", name="A", priority=1),
                Commodity(id="a", name="A2", priority=2),
            ]
        )


def test_lookups_raise_key_error_for_unknown_ids() -> None:
    network = load_network(TINY)
    with pytest.raises(KeyError, match="unknown node id 'zz'"):
        network.node("zz")
    with pytest.raises(KeyError, match="unknown edge id 'zz'"):
        network.edge("zz")
    with pytest.raises(KeyError, match="unknown commodity id 'zz'"):
        load_commodities(TINY / "commodities.json").get("zz")


def test_by_priority_breaks_ties_by_id() -> None:
    commodities = Commodities(
        commodities=[
            Commodity(id="b", name="B", priority=1),
            Commodity(id="a", name="A", priority=1),
            Commodity(id="c", name="C", priority=0 + 1),
        ]
    )
    assert [c.id for c in commodities.by_priority()] == ["a", "b", "c"]


def test_supply_max_below_baseline_and_other_field_limits() -> None:
    with pytest.raises(ValidationError, match="must be >="):
        SupplyRecord(node_id="n", commodity_id="x", tonnes_per_day=5, max_tonnes_per_day=4)
    assert SupplyRecord(node_id="n", commodity_id="x", tonnes_per_day=5, max_tonnes_per_day=5)
    with pytest.raises(ValidationError):
        ReleaseRule(trigger="manual", max_release_fraction_per_day=1.01)
    with pytest.raises(ValidationError):
        ReleaseRule(trigger="sometimes")
    with pytest.raises(ValidationError):
        Scenario(id="s", name="S", horizon_days=0)
    with pytest.raises(ValidationError):
        Scenario(id="s", name="S", horizon_days=5, demand_noise_fraction=1.0)
    with pytest.raises(ValidationError):
        Commodities(commodities=[], import_backlog_loss_fraction_per_day=1.5)
    with pytest.raises(ValidationError):
        Node(id="", name="N", kind="region", lat=0, lon=0)
    with pytest.raises(ValidationError):
        Node(id="n", name="N", kind="region", lat=0, lon=181)


def test_models_are_frozen_and_reject_unknown_fields() -> None:
    edge = Edge(id="e", from_node="a", to_node="b", mode="road", capacity_per_day=1,
                transit_days=1, cost_per_tonne=0)  # fmt: skip
    with pytest.raises(ValidationError):
        edge.capacity_per_day = 5  # type: ignore[misc]
    with pytest.raises(ValidationError):
        Edge.model_validate({**edge.model_dump(), "colour": "red"})


def test_unidirectional_and_bidirectional_arc_expansion_keeps_commodities() -> None:
    edge = Edge(id="e", from_node="a", to_node="b", mode="sea", capacity_per_day=1,
                transit_days=2, cost_per_tonne=0, commodities=frozenset({"x"}))  # fmt: skip
    network = Network(nodes=[], edges=[edge])
    arcs = network.arcs()
    assert [a.id for a in arcs] == ["e", "e:rev"]
    assert all(a.commodities == {"x"} for a in arcs)
