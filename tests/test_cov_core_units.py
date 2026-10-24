"""Unit-level corners: results.summarize, batch, solver scaling, scenario, redirect, engine."""

from pathlib import Path

import pandas as pd
import pytest

from solmu.core.actions import (
    ActionContext,
    ActionError,
    AddCapacity,
    PrioritizeCommodity,
    ReleaseReserve,
    action_record,
    validate_actions,
)
from solmu.core.batch import aggregate, flatten_summary, run_matrix
from solmu.core.engine import Simulation
from solmu.core.loader import load_commodities, load_network, load_policy, load_scenario
from solmu.core.model import (
    Commodities,
    Commodity,
    DemandRecord,
    Network,
    Node,
    Scenario,
    SupplyRecord,
)
from solmu.core.policy import release_step
from solmu.core.redirect import redirect_step
from solmu.core.results import DAILY_COLUMNS, EDGES_COLUMNS, summarize
from solmu.core.scenario import NetworkIds, validate_scenario
from solmu.core.solver import (
    EffectiveArc,
    EffectiveGraph,
    GreedySolver,
    MinCostFlowSolver,
    decompose_flows,
    net_supplies_needs,
    scale_cost,
    scale_tonnes,
    unscale_tonnes,
)
from tests.helpers import chain_policy, chain_sim, manual_policy

TINY = Path(__file__).parent / "fixtures" / "tiny"
X = Commodity(id="x", name="X", priority=1)


def _daily(rows: list[tuple]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=DAILY_COLUMNS)


def _row(day, node, commodity, unmet, demand=10.0) -> tuple:
    return (day, node, commodity, 0.0, 0.0, demand, demand - unmet, unmet, 0.0, 0.0)


def test_summarize_without_any_shortage() -> None:
    daily = _daily([_row(0, "r", "x", 0.0), _row(1, "r", "x", 0.0)])
    edges = pd.DataFrame([(0, "e", 3.0, 10.0, 0.3)], columns=EDGES_COLUMNS)
    summary = summarize(daily, edges, {"r": 100}, holding_cost=2.5, action_spend=0.0)
    assert summary["first_shortage_day"] == {"x": None}
    assert summary["total_unmet"] == 0.0
    assert summary["population_days_in_shortage"] == 0.0
    assert summary["peak_edge_utilization"] == pytest.approx(0.3)
    assert summary["total_cost"] == pytest.approx(2.5)
    assert summary["backlog"] == {} and summary["import_lost_by_commodity"] == {}


def test_summarize_empty_tables_and_missing_population() -> None:
    summary = summarize(_daily([]), pd.DataFrame(columns=EDGES_COLUMNS), {}, 0.0, 0.0)
    assert summary["total_unmet_by_commodity"] == {}
    assert summary["peak_edge_utilization"] == 0.0
    daily = _daily([_row(2, "r", "x", 4.0), _row(2, "r", "y", 1.0), _row(3, "r", "x", 4.0)])
    summary = summarize(daily, pd.DataFrame(columns=EDGES_COLUMNS), {}, 0.0, 0.0)
    assert summary["population_days_in_shortage"] == 0.0  # unknown region counts as 0
    assert summary["first_shortage_day"] == {"x": 2, "y": 2}
    summary = summarize(daily, pd.DataFrame(columns=EDGES_COLUMNS), {"r": 7}, 0.0, 0.0)
    # Region r is short on days 2 and 3: counted once per day, not once per commodity.
    assert summary["population_days_in_shortage"] == 14.0


def test_summarize_passes_backlog_and_loss_through() -> None:
    daily = _daily([_row(0, "r", "x", 0.0)])
    summary = summarize(
        daily, pd.DataFrame(columns=EDGES_COLUMNS), {}, 1.0, 2.0,
        backlog_peak={"x": 5}, import_lost={"x": 1.5},
    )  # fmt: skip
    assert summary["backlog"] == {"x": 5.0}
    assert summary["import_lost_by_commodity"] == {"x": 1.5}
    assert summary["total_cost"] == 3.0


def test_result_before_any_step_is_empty_and_consistent() -> None:
    result = chain_sim().result()
    assert result.daily.empty and result.actions_log == []
    assert result.summary["total_unmet"] == 0.0
    assert result.meta["days_simulated"] == 0
    assert result.meta["solver"] == "MinCostFlowSolver"


def _batch_inputs():
    policy = load_policy(TINY / "policy.json")
    return (
        load_network(TINY),
        load_commodities(TINY / "commodities.json"),
        load_scenario(TINY / "scenario.json"),
        [policy, policy.model_copy(update={"id": "b", "min_stock_days": {}})],
        [0, 1],
    )


def test_run_matrix_with_two_workers_matches_serial() -> None:
    serial = run_matrix(*_batch_inputs())
    parallel = run_matrix(*_batch_inputs(), workers=2)
    pd.testing.assert_frame_equal(serial, parallel)
    assert list(parallel["policy_id"]) == ["tiny_policy", "tiny_policy", "b", "b"]


def test_flatten_summary_columns_and_aggregate_ranges() -> None:
    frame = run_matrix(*_batch_inputs())
    row = flatten_summary(
        {
            "total_unmet": 1.0,
            "total_unmet_by_commodity": {"x": 1.0},
            "first_shortage_day": {"x": None},
            "population_days_in_shortage": 0.0,
            "peak_edge_utilization": 0.5,
            "holding_cost": 1.0,
            "action_spend": 0.0,
            "total_cost": 1.0,
        }
    )
    assert row["unmet_x"] == 1.0 and row["first_shortage_day_x"] is None
    agg = aggregate(frame).set_index("policy_id")
    assert (agg["total_cost_min"] <= agg["total_cost_mean"]).all()
    assert (agg["total_cost_mean"] <= agg["total_cost_max"]).all()


def test_scaling_rounds_tonnes_down_and_cost_to_nearest() -> None:
    assert scale_tonnes(1.99) == 19  # floor(19.9 + 1e-6)
    assert scale_tonnes(0.0999999) == 1  # float dust below the tolerance is kept
    assert scale_tonnes(0.09) == 0
    assert scale_tonnes(0.0) == 0
    assert unscale_tonnes(25) == 2.5
    assert scale_cost(0.004) == 0 and scale_cost(0.005) == 0 and scale_cost(0.006) == 1
    assert scale_cost(1.234) == 123


def _arc(id, a, b, cap, cost=1.0, transit=1) -> EffectiveArc:
    return EffectiveArc(id, id, a, b, cap, cost, transit)


@pytest.mark.parametrize("solver", [GreedySolver, MinCostFlowSolver])
def test_solvers_return_nothing_without_supply_need_or_capacity(solver) -> None:
    graph = EffectiveGraph((_arc("ab", "A", "B", 5.0), _arc("closed", "B", "C", 0.0)))
    assert solver().allocate(graph, {}, {"B": 3.0}, X) == {}
    assert solver().allocate(graph, {"A": 3.0}, {}, X) == {}
    assert solver().allocate(graph, {"A": 3.0}, {"C": 3.0}, X) == {}  # only a closed arc leads on
    assert solver().allocate(EffectiveGraph(()), {"A": 3.0}, {"B": 3.0}, X) == {}


@pytest.mark.parametrize("solver", [GreedySolver, MinCostFlowSolver])
def test_solvers_ignore_demand_that_is_smaller_than_the_resolution(solver) -> None:
    graph = EffectiveGraph((_arc("ab", "A", "B", 5.0),))
    flows = solver().allocate(graph, {"A": 3.0}, {"B": 1e-12}, X)
    assert flows == {}


def test_min_cost_flow_floors_to_a_tenth_of_a_tonne() -> None:
    graph = EffectiveGraph((_arc("ab", "A", "B", 5.0),))
    flows = MinCostFlowSolver().allocate(graph, {"A": 3.0}, {"B": 2.57}, X)
    assert flows == {"ab": pytest.approx(2.5)}
    assert GreedySolver().allocate(graph, {"A": 3.0}, {"B": 2.57}, X) == {"ab": pytest.approx(2.57)}


def test_solvers_use_parallel_arcs_independently() -> None:
    graph = EffectiveGraph(
        (_arc("slow", "A", "B", 4.0, cost=1.0), _arc("fast", "A", "B", 4.0, cost=3.0))
    )
    for solver in (GreedySolver(), MinCostFlowSolver()):
        flows = solver.allocate(graph, {"A": 10.0}, {"B": 6.0}, X)
        assert flows == {"slow": pytest.approx(4.0), "fast": pytest.approx(2.0)}


def test_net_supplies_needs_cancels_at_a_shared_node() -> None:
    supplies, needs, local = net_supplies_needs({"a": 5.0, "b": 2.0}, {"a": 3.0, "c": 4.0})
    assert supplies == {"a": 2.0, "b": 2.0}
    assert needs == {"c": 4.0}
    assert local == {"a": 3.0}
    assert net_supplies_needs({"a": 1.0}, {"a": 1.0}) == ({}, {}, {"a": 1.0})


def test_decompose_flows_drops_pure_cycles_and_handles_empty() -> None:
    graph = EffectiveGraph((_arc("ab", "A", "B", 5.0), _arc("ba", "B", "A", 5.0)))
    assert decompose_flows(graph, {}) == []
    assert decompose_flows(graph, {"ab": 2.0, "ba": 2.0}) == []
    paths = decompose_flows(graph, {"ab": 2.0, "ba": 0.5})
    assert [(p.origin, p.destination, p.tonnes) for p in paths] == [("A", "B", 1.5)]


def test_decompose_splits_flow_over_two_paths() -> None:
    graph = EffectiveGraph(
        (_arc("ab", "A", "B", 5.0), _arc("bd", "B", "D", 5.0), _arc("ad", "A", "D", 5.0))
    )
    paths = decompose_flows(graph, {"ab": 2.0, "bd": 2.0, "ad": 1.0})
    assert sorted((p.arcs, p.tonnes) for p in paths) == [(("ab", "bd"), 2.0), (("ad",), 1.0)]


def _ids() -> NetworkIds:
    return NetworkIds(
        node_ids=frozenset({"n", "r", "h"}),
        region_ids=frozenset({"r"}),
        edge_ids=frozenset({"e"}),
        commodity_ids=frozenset({"x"}),
    )


def test_validate_scenario_lists_every_bad_event_with_index() -> None:
    scenario = Scenario.model_validate(
        {
            "id": "sc",
            "name": "S",
            "horizon_days": 5,
            "events": [
                {"day": 0, "until_day": 1, "type": "node_capacity", "node_id": "n", "factor": 0},
                {"day": 0, "until_day": 1, "type": "node_capacity", "node_id": "zz", "factor": 0},
                {"day": 0, "until_day": 1, "type": "edge_capacity", "edge_id": "q", "factor": 0},
                {
                    "day": 0,
                    "until_day": 1,
                    "type": "demand",
                    "node_id": "h",
                    "commodity_id": "y",
                    "factor": 2,
                },
                {
                    "day": 0,
                    "until_day": 1,
                    "type": "demand",
                    "node_id": "*",
                    "commodity_id": "x",
                    "factor": 2,
                },
            ],
        }
    )
    with pytest.raises(ValueError) as exc:
        validate_scenario(scenario, _ids())
    text = str(exc.value)
    assert "scenario 'sc', event 1 (node_capacity)" in text
    assert "event 2 (edge_capacity)" in text and "'q'" in text
    assert text.count("event 3 (demand)") == 2  # not a region and unknown commodity
    assert "event 0" not in text and "event 4" not in text


def test_validate_scenario_accepts_a_good_one() -> None:
    validate_scenario(Scenario(id="s", name="S", horizon_days=1), _ids())


def test_redirect_without_shortfall_changes_nothing() -> None:
    offers, ready, pending, lost = redirect_step(
        day=3, delay_days=2, baseline={"a": 10.0, "b": 10.0}, direct={"a": 10.0, "b": 10.0},
        cap={"a": 15.0, "b": 15.0}, ready=0.0, pending=[], loss_fraction=0.0,
    )  # fmt: skip
    assert offers == {"a": 10.0, "b": 10.0}
    assert (ready, pending, lost) == (0.0, [], 0.0)


def test_redirect_shortfall_waits_for_the_delay_then_spreads_by_spare_capacity() -> None:
    args = dict(baseline={"a": 10.0, "b": 10.0, "c": 10.0}, cap={"a": 10.0, "b": 14.0, "c": 12.0})
    direct = {"a": 0.0, "b": 10.0, "c": 10.0}
    # Day 0: a's 10 t shortfall is queued for day 2; nothing to place yet.
    offers, ready, pending, lost = redirect_step(0, 2, direct=direct, ready=0.0, pending=[], **args)
    assert offers == direct and ready == 0.0 and pending == [(2, 10.0)] and lost == 0.0
    # Day 2: pool 10 t (plus today's new shortfall of 10 t queued for day 4). Spare is a 10,
    # b 4, c 2 (total 16), so all 10 t go out in proportion: a gets 6.25, b 2.5, c 1.25.
    offers, ready, pending, lost = redirect_step(
        2, 2, direct=direct, ready=0.0, pending=pending, **args
    )
    assert offers == pytest.approx({"a": 6.25, "b": 12.5, "c": 11.25})
    assert ready == 0.0 and pending == [(4, 10.0)]


def test_redirect_caps_by_spare_and_keeps_the_rest_as_backlog_with_loss() -> None:
    offers, ready, pending, lost = redirect_step(
        day=5, delay_days=0, baseline={"a": 10.0, "b": 10.0}, direct={"a": 0.0, "b": 10.0},
        cap={"a": 0.0, "b": 14.0}, ready=6.0, pending=[(9, 3.0)], loss_fraction=0.25,
    )  # fmt: skip
    # Available: 6 ready + 10 new shortfall = 16 t; only b has spare (4 t). 12 t remain,
    # 25% (3 t) are lost, 9 t stay ready; the entry due on day 9 is untouched.
    assert offers == {"a": 0.0, "b": 14.0}
    assert ready == pytest.approx(9.0) and lost == pytest.approx(3.0)
    assert pending == [(9, 3.0)]


def test_redirect_dust_is_cleared() -> None:
    _, ready, pending, _ = redirect_step(
        0, 0, baseline={"a": 1.0}, direct={"a": 1.0}, cap={"a": 1.0}, ready=1e-12, pending=[]
    )
    assert ready == 0.0 and pending == []


def test_release_step_always_and_shortage_edge_cases() -> None:
    from solmu.core.model import ReleaseRule

    key = ("r", "x")
    held = {key: 100.0}
    always = ReleaseRule(trigger="always", max_release_fraction_per_day=0.1)
    assert release_step(always, 0, held, {key: 5.0}, {key: 10.0}, {}, {}) == {key: 10.0}
    assert release_step(always, 0, {key: 0.0}, {key: 5.0}, {key: 10.0}, {}, {}) == {}
    shortage = ReleaseRule(trigger="shortage", delay_days=1, max_release_fraction_per_day=0.5)
    started: dict = {}
    # No unmet yesterday: the clock does not start.
    assert release_step(shortage, 3, held, {key: 0.0}, {key: 10.0}, {key: 0.0}, started) == {}
    assert started == {}
    # Unmet yesterday starts the clock but the delay of 1 day is not over.
    assert release_step(shortage, 4, held, {key: 0.0}, {key: 10.0}, {key: 2.0}, started) == {}
    assert started == {key: 4}
    # Delay over but usable stock covers demand: nothing released.
    assert release_step(shortage, 5, held, {key: 10.0}, {key: 10.0}, {key: 0.0}, started) == {}
    assert release_step(shortage, 5, held, {key: 9.99}, {key: 10.0}, {key: 0.0}, started) == {
        key: 50.0
    }
    manual = ReleaseRule(trigger="manual")
    assert release_step(manual, 5, held, {key: 0.0}, {key: 10.0}, {key: 5.0}, {}) == {}


def test_action_validation_corners() -> None:
    sim = chain_sim(policy=chain_policy(min_stock_days=10, budget=50.0))
    context = sim._action_context()
    assert isinstance(context, ActionContext)
    problems = validate_actions(
        [
            ReleaseReserve("reg", "x", -1.0),
            AddCapacity("e1", -1.0, 0),
            PrioritizeCommodity("x", 0),
        ],
        context,
    )
    assert len(problems) == 4
    assert any("tonnes must be > 0" in p for p in problems)
    assert any("extra_tonnes_per_day must be > 0" in p for p in problems)
    assert any("days must be >= 1" in p for p in problems)
    assert validate_actions([], context) == []
    record = action_record(AddCapacity("e1", 1.0, 2), day=4, cost=20.0)
    assert record == {
        "day": 4,
        "action": "AddCapacity",
        "cost": 20.0,
        "params": {"edge_id": "e1", "extra_tonnes_per_day": 1.0, "days": 2},
    }
    assert ActionError(["a", "b"]).problems == ["a", "b"]


def test_action_cost_preview_and_queue_accumulates_across_apply_calls() -> None:
    sim = chain_sim(policy=chain_policy(min_stock_days=10, budget=100.0))
    assert sim.action_cost(AddCapacity("e1", 2.0, 3)) == 60.0
    assert sim.action_cost(ReleaseReserve("reg", "x", 5.0)) == 0.0
    sim.apply([AddCapacity("e1", 2.0, 3)])
    # The queued 60 counts against the budget: another 60 would exceed 100.
    with pytest.raises(ActionError, match="exceeds budget"):
        sim.apply([AddCapacity("e2", 2.0, 3)])
    # Queued releases count against the strategic stock too.
    sim.apply([ReleaseReserve("reg", "x", 60.0)])
    with pytest.raises(ActionError, match="total ReleaseReserve"):
        sim.apply([ReleaseReserve("reg", "x", 60.0)])


def test_run_until_beyond_the_horizon_is_rejected() -> None:
    sim = chain_sim(horizon=5)
    with pytest.raises(ValueError, match="until=6 exceeds horizon 5"):
        sim.run(until=6)
    assert sim.day == 0


def test_run_until_current_day_is_a_no_op() -> None:
    sim = chain_sim(horizon=5)
    sim.run(until=2)
    result = sim.run(until=2)
    assert sim.day == 2 and result.meta["days_simulated"] == 2


def test_region_with_its_own_supply_is_served_locally_without_transit() -> None:
    network = Network(
        nodes=[Node(id="r", name="R", kind="region", lat=0, lon=0, population=1)],
        edges=[],
        demand=[DemandRecord(node_id="r", commodity_id="x", tonnes_per_day=10)],
        supply=[SupplyRecord(node_id="r", commodity_id="x", tonnes_per_day=10)],
    )
    commodities = Commodities(commodities=[X], default_stock_days=2)
    sim = Simulation(
        network, Scenario(id="s", name="S", horizon_days=4), manual_policy(), commodities
    )
    assert sim.lead_times[("r", "x")] == 0
    result = sim.run()
    # Stock 20 t: each day 10 t are consumed (stock 10), then the local match refills 10 t at
    # dispatch, so the end-of-day stock stays at the 20 t target.
    assert result.summary["total_unmet"] == 0.0
    assert list(result.daily["inbound"]) == pytest.approx([10.0] * 4)
    assert list(result.daily["stock"]) == pytest.approx([20.0] * 4)


def test_greedy_solver_skips_stale_queue_entries_and_finds_the_far_supplier() -> None:
    # Backwards from D, X is first reached at cost 5 (X->D) and then at the cheaper cost 2
    # (X->C->D); the stale entry is skipped and the supplier behind X (cost 10 more) is used.
    graph = EffectiveGraph(
        (
            _arc("xd", "X", "D", 5.0, cost=5.0),
            _arc("xc", "X", "C", 5.0, cost=1.0),
            _arc("cd", "C", "D", 5.0, cost=1.0),
            _arc("yx", "Y", "X", 5.0, cost=10.0),
        )
    )
    flows = GreedySolver().allocate(graph, {"Y": 3.0}, {"D": 3.0}, X)
    assert flows == {"yx": pytest.approx(3.0), "xc": pytest.approx(3.0), "cd": pytest.approx(3.0)}
