"""Results, resumption and reset on a run with hubs, redirection, events and actions."""

from pathlib import Path

import pandas as pd
import pytest

from solmu.core.actions import AddCapacity, PrioritizeCommodity, ReleaseReserve
from solmu.core.engine import Simulation
from solmu.core.loader import load_commodities, load_network
from solmu.core.model import Policy, ReleaseRule
from solmu.core.results import RunResult
from tests.helpers import make_scenario, run_plan

FIXTURES = Path(__file__).parent / "fixtures" / "hubs"
EVENTS = [
    {"day": 3, "type": "node_capacity", "node_id": "p1", "factor": 0.0, "until_day": 14},
    {"day": 4, "type": "demand", "node_id": "*", "commodity_id": "food", "factor": 1.5,
     "until_day": 10},
]  # fmt: skip
POLICY = Policy(
    id="p",
    name="P",
    release=ReleaseRule(trigger="shortage", delay_days=1, max_release_fraction_per_day=0.2),
    min_stock_days={"medicine": 3, "food": 3},
    emergency_budget=500.0,
    holding_cost_per_tonne_day={"medicine": 1.0, "food": 0.2},
)
PLAN = {
    0: [AddCapacity("h1-r1", 10.0, 3)],
    5: [ReleaseReserve("r1", "medicine", 5.0), PrioritizeCommodity("food", 4)],
}


def _sim() -> Simulation:
    return Simulation(
        load_network(FIXTURES),
        make_scenario(EVENTS, 20),
        POLICY,
        load_commodities(FIXTURES / "commodities.json"),
    )


def _same(a: RunResult, b: RunResult) -> None:
    pd.testing.assert_frame_equal(a.daily, b.daily)
    pd.testing.assert_frame_equal(a.edges_daily, b.edges_daily)
    assert a.summary == b.summary
    assert a.actions_log == b.actions_log
    assert a.meta == b.meta


def test_the_scenario_really_exercises_hubs_shortage_backlog_and_actions() -> None:
    result = run_plan(_sim(), PLAN)
    assert result.summary["total_unmet"] > 0
    assert result.summary["backlog"]["medicine"] > 0
    assert result.summary["action_spend"] == pytest.approx(300.0)  # 10 x 3 x 10
    assert len(result.actions_log) == 3
    assert {"h1", "h2"} <= set(result.daily["node_id"])


def test_parquet_round_trip_preserves_everything(tmp_path: Path) -> None:
    result = run_plan(_sim(), PLAN)
    result.to_parquet(tmp_path / "run")
    loaded = RunResult.from_parquet(tmp_path / "run")
    _same(result, loaded)
    assert loaded.summary["first_shortage_day"]["food"] == 6


def test_food_shortage_day_and_release_follow_from_the_arithmetic() -> None:
    # Food: only r1 eats it (5 t/day), only p1 imports it (10 t/day), p1 -> h1 -> r1 takes 2 days
    # (food is not via_hubs here, h1 passes it through), so r1 holds (2 + 2) x 5 = 20 t.
    # Days 0-2: eat 5, order 5 (lands 2 days later). Day 3: p1 closes, the day-1 order lands
    # (stock 15 - 5 = 10). Day 4: the day-2 order lands, demand x1.5 = 7.5 -> 7.5. Day 5: 0.
    # Day 6: 7.5 t unmet - the first food shortage. The shortage clock starts on day 7, delay 1:
    # on day 8 20% of the 3 x 5 = 15 t strategic food is released (3 t), leaving 12 t.
    daily = run_plan(_sim(), PLAN).daily
    food = daily[(daily["node_id"] == "r1") & (daily["commodity_id"] == "food")].set_index("day")
    assert list(food["stock"].iloc[:6]) == pytest.approx([15, 10, 10, 10, 7.5, 0])
    assert list(food["unmet"].iloc[:8]) == pytest.approx([0, 0, 0, 0, 0, 0, 7.5, 7.5])
    assert list(food["strategic_stock"].iloc[6:9]) == pytest.approx([15, 15, 12])


def test_run_until_then_run_equals_a_single_run() -> None:
    whole = _sim()
    whole.apply(PLAN[0])
    expected = whole.run()

    split = _sim()
    split.apply(PLAN[0])
    partial = split.run(until=7)
    assert split.day == 7 and partial.meta["days_simulated"] == 7
    assert partial.daily["day"].max() == 6
    resumed = split.run()
    _same(resumed, expected)


def test_run_until_then_run_with_actions_in_between_equals_a_stepped_run() -> None:
    stepped = run_plan(_sim(), PLAN)
    sim = _sim()
    sim.apply(PLAN[0])
    sim.run(until=5)
    sim.apply(PLAN[5])
    _same(sim.run(), stepped)


def test_reset_after_actions_restores_budget_and_clears_logs() -> None:
    sim = _sim()
    first = run_plan(sim, PLAN)
    assert sim.state.budget == pytest.approx(200.0)
    assert sim.state.action_spend == pytest.approx(300.0)
    sim.reset()
    assert sim.day == 0 and not sim.done
    assert sim.state.budget == 500.0 and sim.state.action_spend == 0.0
    assert sim.state.capacity_boosts == () and sim.state.priority_boosts == ()
    assert sim.state.strategic_stocks[("r1", "medicine")] == pytest.approx(30.0)
    empty = sim.result()
    assert empty.actions_log == [] and empty.daily.empty and empty.edges_daily.empty
    # A queued but unapplied action is dropped by reset as well.
    sim.apply(PLAN[0])
    sim.reset()
    assert sim.run().actions_log == []
    sim.reset()
    _same(run_plan(sim, PLAN), first)
