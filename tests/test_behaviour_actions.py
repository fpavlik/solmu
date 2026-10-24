"""Budget and action stories on the chain network (AddCapacity costs extra x days x 10)."""

import pytest

from solmu.core.actions import ActionError, AddCapacity, PrioritizeCommodity, ReleaseReserve
from tests.helpers import (
    REG,
    bottleneck_chain_sim,
    chain_policy,
    chain_sim,
    run_plan,
    shared_edge_sim,
)

CLOSED_E2 = [{"day": 0, "type": "edge_capacity", "edge_id": "e2", "factor": 0.0, "until_day": 9}]


def test_exactly_affordable_action_is_accepted_and_one_unit_over_is_rejected() -> None:
    # Cost 5 t/day x 3 days x 10 = 150.
    sim = chain_sim(policy=chain_policy(budget=150.0))
    sim.apply([AddCapacity("e1", 5.0, 3)])
    sim.step()
    assert sim.state.budget == pytest.approx(0.0)
    assert sim.state.action_spend == pytest.approx(150.0)

    sim = chain_sim(policy=chain_policy(budget=149.0))
    with pytest.raises(ActionError, match="exceeds budget"):
        sim.apply([AddCapacity("e1", 5.0, 3)])
    sim.step()
    assert sim.state.budget == 149.0
    assert sim.result().actions_log == []


def test_actions_over_several_days_spend_the_budget_down() -> None:
    sim = chain_sim(policy=chain_policy(budget=1000.0), horizon=8)
    plan = {
        0: [AddCapacity("e1", 5.0, 2)],  # 100
        2: [AddCapacity("e2", 10.0, 3)],  # 300
        4: [AddCapacity("e1", 2.0, 5), PrioritizeCommodity("x", 2)],  # 100 + 0
    }
    budgets = []
    while not sim.done:
        if sim.day in plan:
            sim.apply(plan[sim.day])
        budgets.append(sim.step().budget)
    assert budgets == pytest.approx([900, 900, 600, 600, 500, 500, 500, 500])
    log = sim.result().actions_log
    assert [(e["day"], e["action"], e["cost"]) for e in log] == [
        (0, "AddCapacity", 100.0),
        (2, "AddCapacity", 300.0),
        (4, "AddCapacity", 100.0),
        (4, "PrioritizeCommodity", 0.0),
    ]
    assert sim.state.action_spend == pytest.approx(sum(e["cost"] for e in log))
    summary = sim.result().summary
    assert summary["action_spend"] == pytest.approx(500.0)
    assert 1000.0 - sim.state.budget == pytest.approx(summary["action_spend"])


def test_batch_with_one_unaffordable_action_is_rejected_as_a_whole() -> None:
    # Budget 100: each AddCapacity (60) is affordable alone, the pair (120) is not.
    sim = chain_sim(policy=chain_policy(min_stock_days=10, budget=100.0))
    batch = [AddCapacity("e1", 3.0, 2), AddCapacity("e2", 3.0, 2), ReleaseReserve("reg", "x", 5.0)]
    with pytest.raises(ActionError, match="total cost 120.00 exceeds budget 100.00"):
        sim.apply(batch)
    state = sim.step()
    assert state.budget == 100.0 and state.action_spend == 0.0
    assert state.strategic_stocks[REG] == 100.0
    assert sim.result().actions_log == []
    assert state.capacity_boosts == ()


def test_add_capacity_on_the_bottleneck_reduces_unmet_but_not_elsewhere() -> None:
    # e2 carries 4 t/day against 10 t/day demand: stock 50 -> 40, 30, 20, then +4 -10 per day
    # (14, 8, 2) and day 6 is short by 4, days 7-13 by 6 each: 4 + 7 x 6 = 46 t.
    base = bottleneck_chain_sim().run().summary["total_unmet"]
    assert base == pytest.approx(46.0)

    # +6 on e2 gives 10 t/day, enough for demand: cost 6 x 14 x 10 = 840, unmet 0.
    fixed = bottleneck_chain_sim()
    result = run_plan(fixed, {0: [AddCapacity("e2", 6.0, 14)]})
    assert result.summary["total_unmet"] == pytest.approx(0.0)
    assert fixed.state.budget == pytest.approx(1000.0 - 840.0)

    # The same money on e1 (capacity 100, not binding) buys nothing.
    wasted = bottleneck_chain_sim()
    result = run_plan(wasted, {0: [AddCapacity("e1", 6.0, 14)]})
    assert result.summary["total_unmet"] == pytest.approx(base)
    assert wasted.state.budget == pytest.approx(1000.0 - 840.0)

    # A partial boost on the bottleneck helps proportionally less: +2 -> 6 t/day.
    partial = run_plan(bottleneck_chain_sim(), {0: [AddCapacity("e2", 2.0, 14)]})
    assert 0 < partial.summary["total_unmet"] < base


def test_add_capacity_on_a_closed_edge_costs_money_and_changes_nothing() -> None:
    # e2 is closed on days 0-9 (factor 0), boosts are added before the factor: 0 x (100 + 5).
    # Stock 50 lasts days 0-4; days 5-12 are short (shipments of day 10 land on day 13): 80 t.
    plain = bottleneck_chain_sim(e2_capacity=100.0, events=CLOSED_E2)
    base = plain.run()
    assert base.summary["total_unmet"] == pytest.approx(80.0)

    boosted = bottleneck_chain_sim(e2_capacity=100.0, events=CLOSED_E2)
    result = run_plan(boosted, {0: [AddCapacity("e2", 5.0, 2)]})
    assert result.summary["total_unmet"] == pytest.approx(80.0)
    assert result.summary["action_spend"] == pytest.approx(100.0)
    assert boosted.state.budget == pytest.approx(900.0)
    e2 = result.edges_daily[result.edges_daily["edge_id"] == "e2"].set_index("day")
    assert list(e2["flow"].iloc[:10]) == [0.0] * 10
    assert list(e2["capacity"].iloc[:10]) == [0.0] * 10
    assert result.daily.equals(base.daily)


def test_prioritising_the_scarce_commodity_moves_the_shortage() -> None:
    # One 10 t/day edge for 20 t/day of demand. Alone, x (priority 1) takes the whole edge and y
    # (30 t of initial stock) runs dry on day 3: unmet y = 9 days x 10 t = 90 t, x = 0.
    base = shared_edge_sim().run().summary
    assert base["total_unmet_by_commodity"] == {"x": 0.0, "y": pytest.approx(90.0)}
    assert base["first_shortage_day"] == {"x": None, "y": 3}

    sim = shared_edge_sim()
    sim.apply([PrioritizeCommodity("y", 12)])
    boosted = sim.run().summary
    # Mirror image: y never short, x pays the whole bill.
    assert boosted["total_unmet_by_commodity"] == {"x": pytest.approx(90.0), "y": 0.0}
    assert boosted["total_unmet"] == pytest.approx(base["total_unmet"])


def test_short_priority_boost_does_not_conserve_total_unmet() -> None:
    # Unmet is not conserved under priority: a commodity served first keeps its buffer, a
    # commodity that loses the edge eats its buffer, and eaten buffer is demand met.
    # y first on days 0-2: y refills 10 t/day (stock 20 at each day's end), x gets nothing and
    # runs 20, 10, 0. Day 3: x is first again but has 0 t: 10 t unmet, then x ships 10/day and
    # eats exactly what lands (stock 0, unmet 0). y gets no edge: 20, 10, 0 on days 3-5 and is
    # short on days 6-11: 6 x 10 = 60 t. Total 70 t against 90 t with either fixed order.
    sim = shared_edge_sim()
    sim.apply([PrioritizeCommodity("y", 3)])
    summary = sim.run().summary
    assert summary["total_unmet_by_commodity"] == {
        "x": pytest.approx(10.0),
        "y": pytest.approx(60.0),
    }
    assert summary["first_shortage_day"] == {"x": 3, "y": 6}
    assert summary["total_unmet"] == pytest.approx(70.0)


def test_release_reserve_limits_and_strategic_stock_going_to_zero() -> None:
    sim = chain_sim(policy=chain_policy(min_stock_days=10))
    with pytest.raises(ActionError, match="exceeds strategic stock"):
        sim.apply([ReleaseReserve("reg", "x", 100.001)])
    assert sim.step().strategic_stocks[REG] == 100.0

    sim = chain_sim(policy=chain_policy(min_stock_days=10))
    sim.apply([ReleaseReserve("reg", "x", 100.0)])
    state = sim.step()
    assert state.strategic_stocks[REG] == pytest.approx(0.0)
    # 50 usable + 100 released - 10 consumed; the target is 50 so nothing is ordered.
    assert state.stocks[REG] == pytest.approx(140.0)
    with pytest.raises(ActionError, match="exceeds strategic stock"):
        sim.apply([ReleaseReserve("reg", "x", 1.0)])


def test_apply_after_done_is_queued_silently_and_never_applied() -> None:
    # Actual behaviour: apply() does not check `done`; the action sits in the queue, step()
    # raises RuntimeError, and neither budget nor the log ever change.
    sim = chain_sim(policy=chain_policy(budget=100.0), horizon=3)
    sim.run()
    assert sim.done
    sim.apply([AddCapacity("e1", 1.0, 1)])
    with pytest.raises(RuntimeError, match="done"):
        sim.step()
    result = sim.result()
    assert result.actions_log == []
    assert sim.state.budget == 100.0 and sim.state.day == 3
    assert len(result.daily) == 3


def test_zero_budget_rejects_costly_actions_but_allows_free_ones() -> None:
    sim = chain_sim(policy=chain_policy(min_stock_days=10, budget=0.0))
    with pytest.raises(ActionError, match="exceeds budget"):
        sim.apply([AddCapacity("e1", 0.01, 1)])
    sim.apply([ReleaseReserve("reg", "x", 10.0), PrioritizeCommodity("x", 2)])
    sim.step()
    log = sim.result().actions_log
    assert [e["action"] for e in log] == ["ReleaseReserve", "PrioritizeCommodity"]
    assert sim.state.budget == 0.0 and sim.state.action_spend == 0.0
