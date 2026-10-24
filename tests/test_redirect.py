"""Import redirection between ports with a waiting queue.

Ports A and B (10 t/day each, kind port) feed region R (30 t/day, so R always needs more
than the ports offer and every offer is shipped). Port A is closed on days 2..6, the
redirect delay is 2 days: A's 10 t shortfall of day t becomes available on day t + 2.
"""

import pytest

from solmu.core.engine import Simulation
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

CLOSE_A = [{"day": 2, "type": "node_capacity", "node_id": "A", "factor": 0.0, "until_day": 6}]
POLICY = Policy(id="p", name="P", release=ReleaseRule(trigger="manual"))


def _sim(max_a: float | None, max_b: float | None, loss: float = 0.0, horizon: int = 12):
    network = Network(
        nodes=[
            Node(id="A", name="A", kind="port", lat=0, lon=0),
            Node(id="B", name="B", kind="port", lat=1, lon=0),
            Node(id="R", name="R", kind="region", lat=0, lon=1, population=1000),
        ],
        edges=[
            Edge(id=f"e{p}", from_node=p, to_node="R", mode="road", capacity_per_day=1000,
                 transit_days=1, cost_per_tonne=1.0, bidirectional=False)
            for p in "AB"
        ],
        demand=[DemandRecord(node_id="R", commodity_id="x", tonnes_per_day=30)],
        supply=[
            SupplyRecord(node_id=p, commodity_id="x", tonnes_per_day=10, max_tonnes_per_day=m)
            for p, m in (("A", max_a), ("B", max_b))
        ],
    )  # fmt: skip
    commodities = Commodities(
        commodities=[Commodity(id="x", name="X", priority=1)],
        default_stock_days=2,
        import_redirect_delay_days=2,
        import_backlog_loss_fraction_per_day=loss,
    )
    scenario = Scenario.model_validate(
        {"id": "s", "name": "S", "horizon_days": horizon, "events": CLOSE_A}
    )
    return Simulation(network, scenario, POLICY, commodities)


def _flows(sim: Simulation):
    result = sim.run()
    edges = result.edges_daily
    flow = {e: edges[edges["edge_id"] == e].set_index("day")["flow"] for e in ("eA", "eB")}
    return result, flow


def test_port_b_absorbs_redirected_imports_then_backlog_drains_after_reopening() -> None:
    sim = _sim(15, 15)
    backlog = []
    while not sim.done:
        backlog.append(sim.step().import_backlog["x"])
    # Shortfall 10 t/day on days 2..6; the first 10 t is available on day 4. During the
    # closure only B has spare capacity (15 - 10 = 5 t/day), from day 4 on. Backlog = all
    # shortfall so far minus redirected tonnes: 10, 20, 30-5, 40-10, 50-15.
    assert backlog[:7] == pytest.approx([0, 0, 10, 20, 25, 30, 35])
    # A reopens on day 7: spare 5 + 5 = 10 t/day; backlog 35 -> 25 -> 15 -> 5 -> 0.
    assert backlog[7:11] == pytest.approx([25, 15, 5, 0])
    result, flow = _flows(_sim(15, 15))
    assert list(flow["eB"]) == pytest.approx([10, 10, 10, 10, 15, 15, 15, 15, 15, 15, 12.5, 10])
    assert list(flow["eA"]) == pytest.approx([10, 10, 0, 0, 0, 0, 0, 15, 15, 15, 12.5, 10])
    assert result.summary["backlog"] == {"x": pytest.approx(35.0)}
    assert result.summary["import_lost_by_commodity"] == {"x": 0.0}


def test_default_max_means_no_redirection() -> None:
    result, flow = _flows(_sim(None, None))
    assert list(flow["eB"]) == pytest.approx([10] * 12)
    assert list(flow["eA"]) == pytest.approx([10, 10, 0, 0, 0, 0, 0, 10, 10, 10, 10, 10])
    explicit, _ = _flows(_sim(10, 10))
    assert result.daily.equals(explicit.daily)
    assert result.summary["backlog"] == {"x": pytest.approx(50.0)}  # waits, never placed


def test_backlog_loss_fraction_hand_computed() -> None:
    sim = _sim(15, 15, loss=0.2)
    states = [sim.step() for _ in range(6)]
    # Day 4: available 10 t, B takes 5, remaining 5 t loses 20% = 1 t -> ready 4 t. Pending
    # (days 3 and 4 shortfalls) 20 t, so backlog 24 t.
    assert states[4].import_ready["x"] == pytest.approx(4.0)
    assert states[4].import_lost["x"] == pytest.approx(1.0)
    assert states[4].import_backlog["x"] == pytest.approx(24.0)
    # Day 5: available 4 + 10 = 14 t, B takes 5, remaining 9 loses 1.8 -> ready 7.2 t.
    assert states[5].import_ready["x"] == pytest.approx(7.2)
    assert states[5].import_lost["x"] == pytest.approx(2.8)
    assert states[5].import_backlog["x"] == pytest.approx(27.2)
    assert sim.result().summary["import_lost_by_commodity"] == {"x": pytest.approx(2.8)}


def test_loader_rejects_max_below_baseline(tmp_path) -> None:
    from solmu.core.loader import LoadError, load_supply

    path = tmp_path / "supply.csv"
    path.write_text("node_id,commodity_id,tonnes_per_day,max_tonnes_per_day\nA,x,10,5\n")
    with pytest.raises(LoadError, match="max_tonnes_per_day"):
        load_supply(path)
