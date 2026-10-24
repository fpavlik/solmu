"""Policy tests on the chain network (demand 10 t/day, initial usable stock 50 t).

A source cut-off from day 0 for 5 days: shipments of days 5.. arrive from day 8, stock
50 t lasts days 0-4, so days 5, 6, 7 are short by 10 t each (30 t) without a reserve.
"""

import pytest

from tests.helpers import chain_policy, chain_sim

CUT = [{"day": 0, "type": "node_capacity", "node_id": "src", "factor": 0.0, "until_day": 4}]
CUT_FOREVER = [
    {"day": 0, "type": "node_capacity", "node_id": "src", "factor": 0.0, "until_day": 99}
]


def test_no_reserve_means_shortage_after_cutoff() -> None:
    result = chain_sim(CUT, policy=chain_policy(min_stock_days=0), horizon=12).run()
    daily = result.daily.set_index("day")
    assert list(daily["unmet"].iloc[:9]) == pytest.approx([0] * 5 + [10, 10, 10, 0])
    assert result.summary["total_unmet"] == pytest.approx(30.0)


def test_ten_day_reserve_with_always_release_avoids_shortage() -> None:
    policy = chain_policy(min_stock_days=10, trigger="always", max_fraction=0.1)
    result = chain_sim(CUT, policy=policy, horizon=12).run()
    assert result.summary["total_unmet"] == 0.0
    strategic = result.daily.set_index("day")["strategic_stock"]
    assert strategic.iloc[0] == pytest.approx(100 * 0.9)
    assert strategic.iloc[1] == pytest.approx(100 * 0.9 * 0.9)


def test_shortage_release_respects_delay() -> None:
    policy = chain_policy(min_stock_days=10, trigger="shortage", delay_days=2, max_fraction=0.1)
    sim = chain_sim(CUT_FOREVER, policy=policy, horizon=12)
    result = sim.run()
    daily = result.daily.set_index("day")
    # Stock 50 t lasts days 0-4; first unmet on day 5, so the clock starts on day 6 and the
    # first release (10% of 100 t) happens on day 8, not before.
    assert list(daily["unmet"].iloc[:5]) == [0.0] * 5
    assert list(daily["unmet"].iloc[5:8]) == pytest.approx([10, 10, 10])
    assert list(daily["strategic_stock"].iloc[:8]) == pytest.approx([100.0] * 8)
    assert daily["strategic_stock"].iloc[8] == pytest.approx(90.0)
    # Day 8: 10 t released covers demand fully; day 9: 9 t released, 1 t unmet.
    assert daily["unmet"].iloc[8] == pytest.approx(0.0)
    assert daily["strategic_stock"].iloc[9] == pytest.approx(81.0)
    assert daily["unmet"].iloc[9] == pytest.approx(1.0)
    assert sim.state.release_started[("reg", "x")] == 6


def test_shortage_release_with_zero_delay_starts_the_day_after_first_unmet() -> None:
    policy = chain_policy(min_stock_days=10, trigger="shortage", delay_days=0, max_fraction=0.1)
    daily = chain_sim(CUT_FOREVER, policy=policy, horizon=10).run().daily.set_index("day")
    assert daily["strategic_stock"].iloc[5] == pytest.approx(100.0)
    assert daily["strategic_stock"].iloc[6] == pytest.approx(90.0)


def test_manual_trigger_never_releases_automatically() -> None:
    policy = chain_policy(min_stock_days=10, trigger="manual")
    result = chain_sim(CUT_FOREVER, policy=policy, horizon=12).run()
    assert (result.daily["strategic_stock"] == 100.0).all()
    assert result.summary["total_unmet"] > 0
