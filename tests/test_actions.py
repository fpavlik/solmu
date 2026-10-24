import pytest

from solmu.core.actions import (
    ActionError,
    AddCapacity,
    PrioritizeCommodity,
    ReleaseReserve,
)
from solmu.core.engine import Simulation
from solmu.core.model import (
    Commodities,
    Commodity,
    DemandRecord,
    Edge,
    Network,
    Node,
    Scenario,
    SupplyRecord,
)
from tests.helpers import REG, chain_policy, chain_sim


def _sim(budget: float = 1000.0, horizon: int = 8):
    return chain_sim(policy=chain_policy(min_stock_days=10, budget=budget), horizon=horizon)


def test_budget_is_enforced_and_spend_recorded() -> None:
    sim = _sim(budget=100.0)
    # 5 t/day x 3 days x 10 per t-day = 150 > budget 100.
    with pytest.raises(ActionError, match="exceeds budget"):
        sim.apply([AddCapacity("e1", 5.0, 3)])
    sim.step()
    assert sim.state.budget == 100.0 and sim.state.action_spend == 0.0

    sim = _sim(budget=200.0)
    sim.apply([AddCapacity("e1", 5.0, 3)])
    with pytest.raises(ActionError, match="exceeds budget"):
        sim.apply([AddCapacity("e2", 3.0, 2)])  # queued 150 + 60 > 200
    sim.step()
    assert sim.state.budget == pytest.approx(50.0)
    assert sim.state.action_spend == pytest.approx(150.0)
    log = sim.result().actions_log
    assert log == [
        {
            "day": 0,
            "action": "AddCapacity",
            "cost": 150.0,
            "params": {"edge_id": "e1", "extra_tonnes_per_day": 5.0, "days": 3},
        }
    ]
    assert sim.result().summary["action_spend"] == pytest.approx(150.0)
    assert sim.result().summary["total_cost"] == pytest.approx(150.0 + sim.state.holding_cost)


def test_invalid_action_rejects_the_whole_batch() -> None:
    sim = _sim()
    batch = [ReleaseReserve("reg", "x", 30.0), AddCapacity("no-such-edge", 5.0, 3)]
    with pytest.raises(ActionError) as exc:
        sim.apply(batch)
    assert any("no-such-edge" in p for p in exc.value.problems)
    state = sim.step()
    assert state.strategic_stocks[REG] == 100.0
    assert sim.result().actions_log == []


def test_all_problems_are_listed() -> None:
    sim = _sim()
    with pytest.raises(ActionError) as exc:
        sim.apply(
            [
                ReleaseReserve("nowhere", "x", 1.0),
                ReleaseReserve("reg", "gold", 1.0),
                AddCapacity("e1", 0.0, 0),
                PrioritizeCommodity("gold", 1),
            ]
        )
    assert len(exc.value.problems) == 5


def test_release_reserve_moves_stock_and_is_limited_by_strategic_stock() -> None:
    sim = _sim()
    with pytest.raises(ActionError, match="exceeds strategic stock"):
        sim.apply([ReleaseReserve("reg", "x", 100.1)])
    with pytest.raises(ActionError, match="exceeds strategic stock"):
        sim.apply([ReleaseReserve("reg", "x", 60.0), ReleaseReserve("reg", "x", 60.0)])
    sim.apply([ReleaseReserve("reg", "x", 30.0)])
    assert sim.state.stocks[REG] == 50.0  # takes effect at the next step, not at apply()
    state = sim.step()
    # Day 0: stock 50 + 30 released - 10 consumed = 70; target 50 so nothing is ordered.
    assert state.strategic_stocks[REG] == pytest.approx(70.0)
    assert state.stocks[REG] == pytest.approx(70.0)
    assert state.budget == 1000.0


def test_add_capacity_raises_edge_capacity_for_n_days_then_reverts() -> None:
    sim = _sim(horizon=6)
    sim.apply([AddCapacity("e2", 20.0, 3)])
    result = sim.run()
    e2 = result.edges_daily[result.edges_daily["edge_id"] == "e2"].set_index("day")
    assert list(e2["capacity"]) == [120.0, 120.0, 120.0, 100.0, 100.0, 100.0]
    assert sim.state.capacity_boosts == ()


def test_add_capacity_relaxes_a_bottleneck() -> None:
    from tests.helpers import chain_network

    network = chain_network().model_copy(
        update={
            "edges": [
                e.model_copy(update={"capacity_per_day": 5.0}) if e.id == "e2" else e
                for e in chain_network().edges
            ]
        }
    )
    commodities = Commodities(
        commodities=[Commodity(id="x", name="X", priority=1)], default_stock_days=2
    )
    scenario = Scenario(id="s", name="S", horizon_days=4)
    sim = Simulation(network, scenario, chain_policy(), commodities)
    sim.step()
    assert sim.state.edge_flows["e2"] == 5.0
    sim.apply([AddCapacity("e2", 5.0, 1)])
    sim.step()
    # Day 1: need = 50 - 30 - 5 in transit = 15; capacity 5 + 5 = 10 limits the flow.
    assert sim.state.edge_flows["e2"] == 10.0


def test_prioritize_commodity_changes_solver_order() -> None:
    nodes = [
        Node(id="src", name="S", kind="source", lat=0, lon=0),
        Node(id="reg", name="R", kind="region", lat=0, lon=1, population=1),
    ]
    edges = [
        Edge(id="e", from_node="src", to_node="reg", mode="road", capacity_per_day=10,
             transit_days=1, cost_per_tonne=1.0, bidirectional=False)
    ]  # fmt: skip
    network = Network(
        nodes=nodes,
        edges=edges,
        demand=[DemandRecord(node_id="reg", commodity_id=c, tonnes_per_day=10) for c in "xy"],
        supply=[SupplyRecord(node_id="src", commodity_id=c, tonnes_per_day=100) for c in "xy"],
    )
    commodities = Commodities(
        commodities=[
            Commodity(id="x", name="X", priority=1),
            Commodity(id="y", name="Y", priority=2),
        ],
        default_stock_days=2,
    )
    scenario = Scenario(id="s", name="S", horizon_days=3)

    def shipped(actions) -> dict[str, float]:
        sim = Simulation(network, scenario, chain_policy(), commodities)
        sim.apply(actions)
        state = sim.step()
        # Target (2 + 1 lead) x 10 = 30 each; after consumption 20, so each needs 10, but the
        # shared edge carries only 10 t/day: whoever is first in the solver order gets it.
        return {s.commodity_id: s.tonnes for s in state.in_transit}

    assert shipped([]) == {"x": 10.0}
    assert shipped([PrioritizeCommodity("y", 1)]) == {"y": 10.0}
