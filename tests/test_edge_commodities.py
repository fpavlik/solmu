"""Edges restricted to commodities: diamond routing and unreachable regions."""

import pytest

from solmu.core.engine import Simulation
from solmu.core.loader import load_edges
from solmu.core.model import (
    Commodities,
    Commodity,
    DemandRecord,
    Edge,
    Network,
    Node,
    Policy,
    ReleaseRule,
    Scenario,
    SupplyRecord,
)

POLICY = Policy(id="p", name="P", release=ReleaseRule(trigger="manual"))


def _edge(eid: str, a: str, b: str, cost: float, only: set[str] | None) -> Edge:
    return Edge(id=eid, from_node=a, to_node=b, mode="road", capacity_per_day=1000,
                transit_days=1, cost_per_tonne=cost, bidirectional=False,
                commodities=None if only is None else frozenset(only))  # fmt: skip


def _sim(edges: list[Edge], extra_region: bool = False, horizon: int = 8) -> Simulation:
    nodes = [
        Node(id="S", name="S", kind="source", lat=0, lon=0),
        Node(id="M1", name="M1", kind="hub", lat=1, lon=1),
        Node(id="M2", name="M2", kind="hub", lat=-1, lon=1),
        Node(id="R", name="R", kind="region", lat=0, lon=2, population=10),
    ]
    demand = [
        DemandRecord(node_id="R", commodity_id="fuel", tonnes_per_day=10),
        DemandRecord(node_id="R", commodity_id="food", tonnes_per_day=20),
    ]
    if extra_region:
        nodes.append(Node(id="Q", name="Q", kind="region", lat=2, lon=2, population=5))
        demand.append(DemandRecord(node_id="Q", commodity_id="food", tonnes_per_day=4))
        demand.append(DemandRecord(node_id="Q", commodity_id="fuel", tonnes_per_day=2))
    network = Network(
        nodes=nodes,
        edges=edges,
        demand=demand,
        supply=[
            SupplyRecord(node_id="S", commodity_id=c, tonnes_per_day=100) for c in ("fuel", "food")
        ],
    )
    commodities = Commodities(
        commodities=[
            Commodity(id="fuel", name="Fuel", priority=1),
            Commodity(id="food", name="Food", priority=2),
        ],
        default_stock_days=2,
    )
    scenario = Scenario(id="s", name="S", horizon_days=horizon)
    return Simulation(network, scenario, POLICY, commodities)


def _diamond() -> list[Edge]:
    return [
        _edge("cheap1", "S", "M1", 1.0, {"fuel"}),
        _edge("cheap2", "M1", "R", 1.0, {"fuel"}),
        _edge("dear1", "S", "M2", 5.0, None),
        _edge("dear2", "M2", "R", 5.0, None),
    ]


def test_diamond_fuel_takes_cheap_route_food_takes_the_other() -> None:
    result = _sim(_diamond()).run()
    edges = result.edges_daily
    flow = {e: edges[edges["edge_id"] == e].set_index("day")["flow"] for e in
            ("cheap1", "cheap2", "dear1", "dear2")}  # fmt: skip
    # Fuel (10 t/day) uses only the cheap route; food (20 t/day) cannot, so it goes the dear way.
    assert list(flow["cheap1"]) == pytest.approx([10] * 8)
    assert list(flow["cheap2"]) == pytest.approx([10] * 8)
    assert list(flow["dear1"]) == pytest.approx([20] * 8)
    assert list(flow["dear2"]) == pytest.approx([20] * 8)
    assert result.summary["total_unmet"] == 0.0


def test_unreachable_region_for_a_commodity_has_lead_time_zero_and_runs_short() -> None:
    edges = [*_diamond(), _edge("toq", "M1", "Q", 1.0, {"fuel"})]
    sim = _sim(edges, extra_region=True, horizon=6)
    assert sim.lead_times[("Q", "food")] == 0
    assert sim.lead_times[("Q", "fuel")] == 2
    result = sim.run()
    daily = result.daily
    q_food = daily[(daily["node_id"] == "Q") & (daily["commodity_id"] == "food")].set_index("day")
    # Target cover 2 days x 4 t = 8 t, nothing can arrive: stock lasts days 0 and 1.
    assert list(q_food["unmet"]) == pytest.approx([0, 0, 4, 4, 4, 4])
    q_fuel = daily[(daily["node_id"] == "Q") & (daily["commodity_id"] == "fuel")]
    assert q_fuel["unmet"].sum() == 0.0


def test_unknown_commodity_on_edge_is_rejected_and_csv_is_parsed(tmp_path) -> None:
    bad = [_edge("e", "S", "R", 1.0, {"gold"})]
    with pytest.raises(ValueError, match="edge 'e'"):
        _sim(bad)
    path = tmp_path / "edges.csv"
    path.write_text(
        "id,from_node,to_node,mode,capacity_per_day,transit_days,cost_per_tonne,commodities\n"
        "a,S,R,road,10,1,1.0,fuel|food\nb,S,R,road,10,1,1.0,\n"
    )
    a, b = load_edges(path)
    assert a.commodities == {"fuel", "food"}
    assert b.commodities is None
