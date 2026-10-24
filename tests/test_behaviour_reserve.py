"""Reserve release timing as stories on the chain network (demand 10 t/day, usable stock 50 t).

Strategic stock is 10 days x 10 t = 100 t. The source is cut on days 0..4, shipments resume on
day 5 and arrive on day 8, so without help days 5, 6, 7 are short by 10 t each (30 t). Under
`manual` only explicit `ReleaseReserve` actions move stock; holding cost is 0.5 per strategic
tonne-day (applied to end-of-day strategic stock, usable stock is free).
"""

import pytest

from solmu.core.actions import ReleaseReserve
from tests.helpers import REG, chain_policy, chain_sim, run_plan

CUT = [{"day": 0, "type": "node_capacity", "node_id": "src", "factor": 0.0, "until_day": 4}]
CUT_FOREVER = [
    {"day": 0, "type": "node_capacity", "node_id": "src", "factor": 0.0, "until_day": 99}
]
HORIZON = 14


def _manual(plan: dict[int, float], events=CUT):
    sim = chain_sim(events, policy=chain_policy(min_stock_days=10), horizon=HORIZON)
    result = run_plan(sim, {d: [ReleaseReserve("reg", "x", t)] for d, t in plan.items()})
    return sim, result


def _unmet(plan: dict[int, float]) -> float:
    return _manual(plan)[1].summary["total_unmet"]


def test_release_on_day_zero_is_not_wasted_and_prevents_the_whole_shortage() -> None:
    # The model has no spoilage and usable stock is never discarded when it is above target:
    # 30 t released on day 0 sit in usable stock (80 t instead of 50 t), so the stock lasts
    # until day 7 and the day-5 shipment (need = 50 - 20 = 30 t) lands on day 8. Unmet = 0.
    # Early release therefore equals just-in-time release in unmet terms.
    _, early = _manual({0: 30.0})
    assert early.summary["total_unmet"] == pytest.approx(0.0)
    assert _unmet({}) == pytest.approx(30.0)
    assert _unmet({0: 30.0}) == pytest.approx(_unmet({4: 30.0}))


def test_early_release_only_lowers_holding_cost() -> None:
    # Strategic stock after release is 70 t from the release day on (30 t x 0.5 = 15 saved per
    # day left); the earlier the release, the cheaper the run, with identical unmet.
    holding = {d: _manual({d: 30.0})[1].summary["holding_cost"] for d in (0, 2, 4)}
    # Release on day d: 100 t for d days, 70 t for the other 14 - d days, x 0.5.
    for d, cost in holding.items():
        assert cost == pytest.approx(0.5 * (100 * d + 70 * (HORIZON - d)))
    assert holding[0] < holding[2] < holding[4] < _manual({})[1].summary["holding_cost"]


def test_release_just_in_time_covers_exactly_the_released_tonnes() -> None:
    # Released on day 5 (the first short day; actions apply before consumption): 10 t cover
    # day 5, 20 t cover days 5-6, 30 t cover days 5-7. More than the 30 t gap changes nothing.
    base = _unmet({})
    assert _unmet({5: 10.0}) == pytest.approx(base - 10.0)
    assert _unmet({5: 20.0}) == pytest.approx(base - 20.0)
    assert _unmet({5: 30.0}) == pytest.approx(0.0)
    assert _unmet({5: 60.0}) == pytest.approx(0.0)


def test_release_one_day_before_the_shortage_gives_the_same_result() -> None:
    assert _unmet({4: 30.0}) == pytest.approx(_unmet({5: 30.0})) == pytest.approx(0.0)


def test_release_too_late_has_no_effect_on_unmet() -> None:
    # The shortage ends on day 7. A release on day 8 or later finds no unmet demand.
    base = _unmet({})
    for day in (8, 9, 12):
        sim, result = _manual({day: 30.0})
        assert result.summary["total_unmet"] == pytest.approx(base)
        assert sim.state.strategic_stocks[REG] == pytest.approx(70.0)


def test_release_during_the_shortage_split_or_lump_gives_equal_unmet() -> None:
    # Day 5 is already lost (nothing released). Days 6-7 need 20 t: a lump of 20 t on day 6 and
    # 10 t on each of days 6 and 7 both leave only the day-5 unmet of 10 t.
    lump = _unmet({6: 20.0})
    split = _unmet({6: 10.0, 7: 10.0})
    assert lump == pytest.approx(10.0)
    assert split == pytest.approx(10.0)
    # Releasing more than needed by a lump costs nothing extra in unmet, only in usable stock.
    assert _unmet({6: 50.0}) == pytest.approx(10.0)
    # A split that stops short (10 t on day 7 only) leaves day 6 and day 5 short: 20 t.
    assert _unmet({7: 10.0}) == pytest.approx(20.0)


def test_automatic_release_unmet_grows_with_the_delay() -> None:
    # Cut forever; strategic 100 t, release fraction 1. First unmet day is 5, the clock starts
    # on day 6 and the release happens on day 6 + delay, so days 5 .. 5 + delay are short:
    # unmet = 10 x (delay + 1).
    totals = []
    for delay in (0, 2, 5):
        policy = chain_policy(min_stock_days=10, trigger="shortage", delay_days=delay)
        result = chain_sim(CUT_FOREVER, policy=policy, horizon=HORIZON).run()
        totals.append(result.summary["total_unmet"])
        assert totals[-1] == pytest.approx(10.0 * (delay + 1))
    assert totals == sorted(totals) and len(set(totals)) == 3


def test_larger_release_fraction_gives_less_or_equal_unmet() -> None:
    # Fraction 0.05: day 6 releases 5 t (unmet 5), day 7 releases 0.05 x 95 = 4.75 (unmet 5.25).
    # Fraction 0.5: day 6 releases 50 t, which covers days 6-10 outright.
    results = {}
    for fraction in (0.05, 0.5):
        policy = chain_policy(
            min_stock_days=10, trigger="shortage", delay_days=0, max_fraction=fraction
        )
        results[fraction] = chain_sim(CUT_FOREVER, policy=policy, horizon=HORIZON).run()
    slow = results[0.05].daily.set_index("day")["unmet"]
    assert list(slow.iloc[5:8]) == pytest.approx([10.0, 5.0, 5.25])
    fast = results[0.5].daily.set_index("day")["unmet"]
    assert list(fast.iloc[5:11]) == pytest.approx([10.0, 0, 0, 0, 0, 0])
    assert results[0.5].summary["total_unmet"] <= results[0.05].summary["total_unmet"]
    assert results[0.5].summary["total_unmet"] < results[0.05].summary["total_unmet"] - 40


def test_always_trigger_drains_the_reserve_and_cuts_holding_cost() -> None:
    always = chain_sim(
        CUT, policy=chain_policy(min_stock_days=10, trigger="always", max_fraction=0.1),
        horizon=HORIZON,
    ).run()  # fmt: skip
    manual = chain_sim(CUT, policy=chain_policy(min_stock_days=10), horizon=HORIZON).run()
    strategic = always.daily.set_index("day")["strategic_stock"]
    assert list(strategic.iloc[:3]) == pytest.approx([90.0, 81.0, 72.9])
    assert strategic.is_monotonic_decreasing
    # Manual keeps 100 t for 14 days: 100 x 0.5 x 14. Always: sum of 100 x 0.9^d x 0.5, d = 1..14
    # = 450 x (1 - 0.9^14).
    assert manual.summary["holding_cost"] == pytest.approx(700.0)
    assert always.summary["holding_cost"] == pytest.approx(450.0 * (1 - 0.9**14))
    assert always.summary["holding_cost"] < manual.summary["holding_cost"]
    assert always.summary["total_unmet"] == 0.0
