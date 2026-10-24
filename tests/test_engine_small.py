"""Engine tests on a 3-node chain (source -> hub -> region) with hand-computed numbers.

Setup: one commodity `x`, default_stock_days = 2, region demand 10 t/day. Lead time of
the region is 1 + 2 = 3 days, so target = (2 + 3) x 10 = 50 t, which is also the initial
usable stock. Source supplies 30 t/day. A dispatch on day d arrives on day d + 3.
"""

from pathlib import Path

import pytest

from solmu import __version__
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
from solmu.core.results import RunResult
from solmu.core.solver import GreedySolver, MinCostFlowSolver

REG = ("reg", "x")


def _network(demand: float = 10.0, supply: float = 30.0) -> Network:
    return Network(
        nodes=[
            Node(id="src", name="Source", kind="source", lat=0, lon=0),
            Node(id="hub", name="Hub", kind="hub", lat=0, lon=1),
            Node(id="reg", name="Region", kind="region", lat=0, lon=2, population=1000),
        ],
        edges=[
            Edge(id="e1", from_node="src", to_node="hub", mode="road", capacity_per_day=100,
                 transit_days=1, cost_per_tonne=1.0, bidirectional=False),
            Edge(id="e2", from_node="hub", to_node="reg", mode="rail", capacity_per_day=100,
                 transit_days=2, cost_per_tonne=1.0, bidirectional=False),
        ],
        demand=[DemandRecord(node_id="reg", commodity_id="x", tonnes_per_day=demand)],
        supply=[SupplyRecord(node_id="src", commodity_id="x", tonnes_per_day=supply)],
    )  # fmt: skip


def _sim(solver, min_stock_days: float = 0.0, horizon: int = 5) -> Simulation:
    commodities = Commodities(
        commodities=[Commodity(id="x", name="X", priority=1)], default_stock_days=2
    )
    policy = Policy(
        id="p",
        name="P",
        min_stock_days={"x": min_stock_days},
        release=ReleaseRule(trigger="manual"),
        emergency_budget=100.0,
        holding_cost_per_tonne_day={"x": 0.5},
    )
    scenario = Scenario(id="s", name="S", horizon_days=horizon)
    return Simulation(_network(), scenario, policy, commodities, solver=solver)


SOLVERS = [GreedySolver, MinCostFlowSolver]


@pytest.mark.parametrize("solver", SOLVERS)
def test_five_day_stocks_and_unmet_with_transit_delay(solver) -> None:
    sim = _sim(solver())
    assert sim.lead_times[REG] == 3
    assert sim.state.stocks[REG] == 50.0
    assert sim.state.target_stocks[REG] == 50.0

    # Day 0: consume 10 -> stock 40; need = 50 - 40 - 0 = 10; ship 10, arrives day 3.
    # Day 1: consume 10 -> stock 30; need = 50 - 30 - 10 in transit = 10; ship (arrives day 4).
    # Day 2: consume 10 -> stock 20; need = 50 - 20 - 20 = 10; ship (arrives day 5).
    # Day 3: arrival 10 -> 30, consume -> 20; need = 50 - 20 - 20 (days 4, 5) = 10; ship (day 6).
    # Day 4: arrival 10 -> 30, consume -> 20; need = 50 - 20 - 20 (days 5, 6) = 10; ship (day 7).
    # The pipeline never starves: unmet is 0 every day.
    expected_stock = [40, 30, 20, 20, 20]
    expected_unmet = [0, 0, 0, 0, 0]
    expected_flow = [10, 10, 10, 10, 10]
    expected_in_transit = [10, 20, 30, 30, 30]
    for d in range(5):
        state = sim.step()
        assert state.day == d + 1
        assert state.stocks[REG] == pytest.approx(expected_stock[d])
        assert state.unmet[REG] == pytest.approx(expected_unmet[d])
        assert state.edge_flows["e1"] == pytest.approx(expected_flow[d])
        assert state.edge_flows["e2"] == pytest.approx(expected_flow[d])
        assert sum(s.tonnes for s in state.in_transit) == pytest.approx(expected_in_transit[d])
    assert sim.done
    assert sim.state.cumulative_unmet["x"] == pytest.approx(0.0)

    result = sim.result()
    daily = result.daily.set_index("day")
    assert list(daily["unmet"]) == pytest.approx(expected_unmet)
    assert list(daily["inbound"]) == pytest.approx([0, 0, 0, 10, 10])
    assert list(daily["consumed"]) == pytest.approx([10, 10, 10, 10, 10])
    assert result.summary["total_unmet_by_commodity"] == {"x": pytest.approx(0.0)}
    assert result.summary["first_shortage_day"] == {"x": None}
    assert result.summary["population_days_in_shortage"] == 0
    assert result.summary["peak_edge_utilization"] == pytest.approx(0.1)
    assert result.meta["code_version"] == __version__


@pytest.mark.parametrize("solver", SOLVERS)
def test_strategic_stock_is_held_separately_and_costs_money(solver) -> None:
    sim = _sim(solver(), min_stock_days=3)
    # Strategic stock = 3 days x 10 t = 30 t, never consumed; holding = 30 x 0.5 = 15 per day.
    assert sim.state.strategic_stocks[REG] == 30.0
    assert sim.state.stocks[REG] == 50.0
    sim.run()
    assert sim.state.strategic_stocks[REG] == 30.0
    assert sim.state.holding_cost == pytest.approx(75.0)
    assert sim.result().summary["total_cost"] == pytest.approx(75.0)
    assert sim.state.cumulative_unmet["x"] == pytest.approx(0.0)


def test_step_returns_new_states_and_never_mutates_old_ones() -> None:
    sim = _sim(MinCostFlowSolver())
    s0 = sim.state
    s1 = sim.step()
    s2 = sim.step()
    assert s0 is not s1 and s1 is not s2
    assert (s0.day, s1.day, s2.day) == (0, 1, 2)
    assert s0.stocks[REG] == 50.0
    assert s0.in_transit == ()
    assert s1.stocks[REG] == 40.0
    assert sum(s.tonnes for s in s1.in_transit) == 10.0
    with pytest.raises(TypeError):
        s1.stocks[REG] = 0.0  # type: ignore[index]
    with pytest.raises(AttributeError):
        s1.day = 5  # type: ignore[misc]


def test_run_produces_full_tables_and_done_at_horizon() -> None:
    sim = _sim(MinCostFlowSolver(), horizon=7)
    assert not sim.done
    result = sim.run()
    assert sim.done and sim.day == 7
    assert len(result.daily) == 7 * 1 * 1
    assert len(result.edges_daily) == 7 * 2
    assert list(result.daily.columns) == [
        "day", "node_id", "commodity_id", "stock", "strategic_stock",
        "demand", "consumed", "unmet", "inbound", "outbound",
    ]  # fmt: skip
    with pytest.raises(RuntimeError):
        sim.step()


def test_run_until_and_reset() -> None:
    sim = _sim(MinCostFlowSolver())
    sim.run(until=2)
    assert sim.day == 2 and not sim.done
    first = sim.run().daily
    sim.reset()
    assert sim.day == 0
    assert sim.run().daily.equals(first)


def test_result_parquet_round_trip(tmp_path: Path) -> None:
    result = _sim(MinCostFlowSolver()).run()
    result.to_parquet(tmp_path / "out")
    loaded = RunResult.from_parquet(tmp_path / "out")
    assert loaded.daily.equals(result.daily)
    assert loaded.edges_daily.equals(result.edges_daily)
    assert loaded.summary == result.summary
    assert loaded.meta == result.meta
    assert loaded.actions_log == []


def test_tiny_fixture_30_day_run_is_fast_and_complete() -> None:
    import time

    from solmu.core.loader import load_commodities, load_network, load_policy, load_scenario

    fx = Path(__file__).parent / "fixtures" / "tiny"
    scenario = load_scenario(fx / "scenario.json").model_copy(update={"horizon_days": 30})
    sim = Simulation(
        load_network(fx),
        scenario,
        load_policy(fx / "policy.json"),
        load_commodities(fx / "commodities.json"),
    )
    start = time.perf_counter()
    result = sim.run()
    assert time.perf_counter() - start < 1.0
    assert len(result.daily) == 30 * 2 * 2  # days x regions x commodities


def test_tiny_fixture_baseline_30_days_has_zero_unmet() -> None:
    from solmu.core.loader import load_commodities, load_network, load_policy, load_scenario

    fx = Path(__file__).parent / "fixtures" / "tiny"
    scenario = load_scenario(fx / "scenario.json").model_copy(
        update={"horizon_days": 30, "events": []}
    )
    sim = Simulation(
        load_network(fx),
        scenario,
        load_policy(fx / "policy.json"),
        load_commodities(fx / "commodities.json"),
    )
    result = sim.run()
    assert result.summary["total_unmet"] == 0.0
    # reg-a grain: src->hub 1 + hub->reg-a 2 = 3 days; reg-b: 1 + 1 = 2 days.
    assert sim.lead_times[("reg-a", "grain")] == 3
    assert sim.lead_times[("reg-b", "grain")] == 2
    assert sim.lead_times[("reg-b", "pills")] == 2
