"""Anticipatory release (`on_disruption`, spec 13.9) on a two-port synthetic network.

Ports A and B import 10 t/day each of commodity `x` (no handling headroom unless stated) and
feed region R by 1-day edges; demand 20 t/day, default cover 2 days, lead time 1 day, so the
initial usable stock is (2 + 1) x 20 = 60 t. Strategic stock: 5 days x 20 = 100 t, released at
10% of the remaining stock per day. Closing port A (factor 0) halves the import offer: the
import drop is 1 - 10/20 = 0.5.
"""

import pytest
from pydantic import ValidationError

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
    SupplyRecord,
)
from solmu.core.policy import disruption_step
from solmu.core.results import RunResult
from tests.helpers import make_scenario


def two_port_network(max_b: float = 10.0) -> Network:
    return Network(
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
        demand=[DemandRecord(node_id="R", commodity_id="x", tonnes_per_day=20)],
        supply=[
            SupplyRecord(node_id="A", commodity_id="x", tonnes_per_day=10),
            SupplyRecord(
                node_id="B", commodity_id="x", tonnes_per_day=10, max_tonnes_per_day=max_b
            ),
        ],
    )  # fmt: skip


def closure(first: int, last: int, factor: float = 0.0, port: str = "A") -> dict:
    return {
        "day": first,
        "until_day": last,
        "type": "node_capacity",
        "node_id": port,
        "factor": factor,
    }


def policy(trigger: str = "on_disruption", **release) -> Policy:
    release = {"delay_days": 2, "max_release_fraction_per_day": 0.1, **release}
    return Policy(
        id="p",
        name="P",
        min_stock_days={"x": 5.0},
        release=ReleaseRule(trigger=trigger, **release),
        holding_cost_per_tonne_day={"x": 0.5},
    )


def make_sim(events: list[dict], pol: Policy, horizon: int = 30, max_b: float = 10.0) -> Simulation:
    commodities = Commodities(
        commodities=[Commodity(id="x", name="X", priority=1)], default_stock_days=2
    )
    return Simulation(two_port_network(max_b), make_scenario(events, horizon), pol, commodities)


def strategic(result: RunResult) -> list[float]:
    return list(result.daily.set_index("day")["strategic_stock"])


def test_defaults_keep_shortage_trigger_semantics_and_validate_new_fields() -> None:
    rule = ReleaseRule(trigger="on_disruption")
    assert (rule.disruption_threshold, rule.disruption_persistence_days) == (0.3, 1)
    with pytest.raises(ValidationError):
        ReleaseRule(trigger="on_disruption", disruption_persistence_days=0)
    with pytest.raises(ValidationError):
        ReleaseRule(trigger="on_disruption", disruption_threshold=1.5)


def test_drop_below_threshold_means_no_release() -> None:
    # Port A at 0.5: offer 10 + 5 = 15, drop 1 - 15/20 = 0.25 < 0.3.
    result = make_sim([closure(2, 29, factor=0.5)], policy(), horizon=12).run()
    assert strategic(result) == pytest.approx([100.0] * 12)
    assert result.imports_daily["drop"].iloc[5] == pytest.approx(0.25)


def test_drop_above_threshold_starts_countdown_and_releases_after_delay() -> None:
    # Closure from day 2: drop 0.5 > 0.3 on day 2, so the countdown starts on day 2 and the
    # first release (10% of 100 t) is on day 2 + 2 = 4, then 10% of 90 t on day 5.
    sim = make_sim([closure(2, 29)], policy(), horizon=12)
    result = sim.run()
    assert strategic(result)[:6] == pytest.approx([100, 100, 100, 100, 90, 81])
    assert sim.state.disruption_started == {"x": 2}
    assert sim.state.import_drop["x"] == pytest.approx(0.5)


@pytest.mark.parametrize(("delay", "persistence"), [(0, 1), (3, 1), (0, 2), (2, 3)])
def test_first_release_lands_on_start_plus_delay(delay: int, persistence: int) -> None:
    # Streak reaches `persistence` on day 2 + persistence - 1 (the countdown start).
    start = 2 + persistence - 1
    pol = policy(delay_days=delay, disruption_persistence_days=persistence)
    result = make_sim([closure(2, 29)], pol, horizon=14).run()
    series = strategic(result)
    first_release = next(d for d, v in enumerate(series) if v < 100.0)
    assert first_release == start + delay
    assert series[first_release] == pytest.approx(90.0)


def test_release_pauses_when_disruption_ends_and_resumes_when_it_returns() -> None:
    # Closure days 2-4 (stock 60 t covers the 10 t/day deficit: no unmet), delay 0: releases on
    # days 2, 3, 4 (100 -> 90 -> 81 -> 72.9). Day 5: drop 0 and no unmet, so release pauses.
    # The clock is not reset: the second closure on days 8-9 releases from its first day.
    sim = make_sim([closure(2, 4), closure(8, 9)], policy(delay_days=0), horizon=14)
    result = sim.run()
    assert result.summary["total_unmet"] == 0.0
    series = strategic(result)
    assert series[:8] == pytest.approx([100, 100, 90, 81, 72.9, 72.9, 72.9, 72.9])
    assert series[8] == pytest.approx(72.9 * 0.9)
    assert series[9] == pytest.approx(72.9 * 0.81)
    assert series[10:] == pytest.approx([72.9 * 0.81] * 4)
    assert sim.state.disruption_started == {"x": 2}


def test_release_continues_while_unmet_demand_remains_after_disruption_ends() -> None:
    # Both ports shut days 2-9, delay 0: 10% of the remaining 100 t is released every closed day
    # (days 2-9, eight releases). Stock 60 + releases 10, 9, 8.1, 7.29, 6.56 against 20/day runs
    # out on day 7, so days 7-9 are short. Day 10: ports reopen (drop 0) but day 9 was short, so
    # the ninth release happens and the first shipment is dispatched (lands day 11); day 10 is
    # short too, so day 11 releases a tenth time. Day 11 is covered (20 t land), so from day 12
    # nothing more is released: strategic stock stays at 100 x 0.9^10.
    events = [closure(2, 9), closure(2, 9, port="B")]
    result = make_sim(events, policy(delay_days=0), horizon=20).run()
    daily = result.daily.set_index("day")
    drops = result.imports_daily.set_index("day")["drop"]
    assert list(drops.iloc[8:13]) == [1.0, 1.0, 0.0, 0.0, 0.0]
    assert [daily["unmet"][d] > 0 for d in (6, 7, 9, 10, 11)] == [False, True, True, True, False]
    series = strategic(result)
    assert series[9] == pytest.approx(100 * 0.9**8)
    assert series[10] == pytest.approx(100 * 0.9**9)
    assert series[11] == pytest.approx(100 * 0.9**10)
    assert series[12:] == pytest.approx([100 * 0.9**10] * 8)


def test_persistence_three_ignores_a_two_day_blip() -> None:
    result = make_sim([closure(2, 3)], policy(disruption_persistence_days=3), horizon=12).run()
    assert strategic(result) == pytest.approx([100.0] * 12)
    exact = make_sim([closure(2, 4)], policy(delay_days=0, disruption_persistence_days=3), 12)
    assert strategic(exact.run())[:5] == pytest.approx([100, 100, 100, 100, 90])


def test_streak_resets_between_blips() -> None:
    events = [closure(2, 3), closure(5, 6)]
    result = make_sim(events, policy(disruption_persistence_days=3), horizon=12).run()
    assert strategic(result) == pytest.approx([100.0] * 12)


def test_earlier_than_shortage_trigger_on_the_same_crisis() -> None:
    events = [closure(2, 29)]
    on = make_sim(events, policy(), horizon=30).run()
    late = make_sim(events, policy("shortage"), horizon=30).run()
    no_reserve = make_sim(events, policy("manual"), horizon=30).run()
    first_on = on.summary["first_shortage_day"]["x"]
    first_late = late.summary["first_shortage_day"]["x"]
    assert first_late == no_reserve.summary["first_shortage_day"]["x"]
    assert first_on is None or first_on > first_late
    assert on.summary["total_unmet"] <= late.summary["total_unmet"]
    assert on.summary["total_unmet"] < no_reserve.summary["total_unmet"]


def test_holding_cost_falls_as_stock_is_released() -> None:
    # 0.5 per tonne-day on end-of-day strategic stock: days 0-3 at 100 t, day 4 at 90 t.
    sim = make_sim([closure(2, 29)], policy(), horizon=12)
    sim.run(until=5)
    assert sim.state.holding_cost == pytest.approx(4 * 50.0 + 45.0)
    result = sim.run()
    held = make_sim([closure(2, 29)], policy("manual"), horizon=12).run()
    assert result.summary["holding_cost"] < held.summary["holding_cost"]
    assert held.summary["holding_cost"] == pytest.approx(12 * 50.0)


def test_import_drop_for_one_closed_port_matches_the_arithmetic() -> None:
    sim = make_sim([closure(2, 3)], policy(), horizon=6)
    result = sim.run()
    table = result.imports_daily.set_index("day")
    assert list(table["baseline"]) == pytest.approx([20.0] * 6)
    assert list(table["offer"]) == pytest.approx([20, 20, 10, 10, 20, 20])
    assert list(table["drop"]) == pytest.approx([0, 0, 0.5, 0.5, 0, 0])


def test_import_drop_is_measured_before_redirection() -> None:
    # B has headroom (cap 20), so redirected cargo lifts its offer after the 2-day delay, but
    # the drop is still 0.5 on every closed day.
    result = make_sim([closure(2, 9)], policy("manual"), horizon=10, max_b=20.0).run()
    assert list(result.imports_daily["drop"].iloc[2:10]) == pytest.approx([0.5] * 8)
    assert result.summary["backlog"]["x"] > 0


def test_imports_daily_round_trips_through_parquet(tmp_path) -> None:
    result = make_sim([closure(2, 3)], policy(), horizon=6).run()
    result.to_parquet(tmp_path / "out")
    loaded = RunResult.from_parquet(tmp_path / "out")
    assert loaded.imports_daily.equals(result.imports_daily)


def test_disruption_step_is_a_pure_function() -> None:
    rule = ReleaseRule(trigger="on_disruption", delay_days=1, disruption_persistence_days=2)
    assert disruption_step(rule, 0, 0.5, 0, None, False) == (1, None, False)
    assert disruption_step(rule, 1, 0.5, 1, None, False) == (2, 1, False)
    assert disruption_step(rule, 2, 0.5, 2, 1, False) == (3, 1, True)
    assert disruption_step(rule, 3, 0.0, 3, 1, False) == (0, 1, False)
    assert disruption_step(rule, 4, 0.0, 0, 1, True) == (0, 1, True)
