"""Shortage cost: unmet demand priced per commodity into the summary (spec extension, opt-in)."""

import pandas as pd
import pytest

from solmu.core.engine import Simulation
from solmu.core.model import Commodities, Commodity
from solmu.core.results import DAILY_COLUMNS, EDGES_COLUMNS, summarize
from tests.helpers import chain_network, chain_policy, make_scenario


def _daily(rows: list[tuple[int, str, str, float]]) -> pd.DataFrame:
    frame = pd.DataFrame(rows, columns=["day", "node_id", "commodity_id", "unmet"])
    for column in DAILY_COLUMNS:
        if column not in frame:
            frame[column] = 0.0
    return frame[DAILY_COLUMNS]


def test_summary_prices_unmet_per_commodity() -> None:
    # x: 3 + 4 = 7 t unmet at 10 per tonne = 70; y: 2 t at 0.5 = 1; z: never short, costs 0.
    daily = _daily([(0, "r", "x", 3.0), (1, "r", "x", 4.0), (1, "r", "y", 2.0), (0, "r", "z", 0.0)])
    summary = summarize(
        daily,
        pd.DataFrame(columns=EDGES_COLUMNS),
        {"r": 1},
        holding_cost=2.5,
        action_spend=4.0,
        shortage_cost_per_tonne={"x": 10.0, "y": 0.5, "z": 100.0},
    )
    assert summary["shortage_cost_by_commodity"] == {"x": 70.0, "y": 1.0, "z": 0.0}
    assert summary["shortage_cost"] == 71.0
    assert summary["total_cost"] == pytest.approx(2.5 + 4.0 + 71.0)


def test_default_prices_nothing() -> None:
    daily = _daily([(0, "r", "x", 3.0)])
    summary = summarize(daily, pd.DataFrame(columns=EDGES_COLUMNS), {"r": 1}, 2.5, 0.0)
    assert summary["shortage_cost"] == 0.0
    assert summary["total_cost"] == 2.5


def test_commodity_field_defaults_to_zero() -> None:
    assert Commodity(id="x", name="X", priority=1).shortage_cost_per_tonne == 0.0
    with pytest.raises(ValueError):
        Commodity(id="x", name="X", priority=1, shortage_cost_per_tonne=-1)


def test_engine_total_cost_includes_priced_shortage() -> None:
    # Cutting the only edge for the whole run empties the 50 t start stock after 5 days;
    # 12-day horizon leaves 7 days x 10 t = 70 t unmet, priced at 3 per tonne = 210.
    priced = Commodities(
        commodities=[Commodity(id="x", name="X", priority=1, shortage_cost_per_tonne=3.0)],
        default_stock_days=2,
    )
    cut = [{"day": 0, "type": "edge_capacity", "edge_id": "e1", "factor": 0.0, "until_day": 11}]
    sim = Simulation(chain_network(), make_scenario(cut, 12), chain_policy(), priced, seed=0)
    result = sim.run()
    assert result.summary["total_unmet"] == pytest.approx(70.0)
    assert result.summary["shortage_cost"] == pytest.approx(210.0)
    assert result.summary["total_cost"] == pytest.approx(
        result.summary["holding_cost"] + result.summary["action_spend"] + 210.0
    )
