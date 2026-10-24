"""Tests for the policy x seed batch runner."""

from pathlib import Path

import pandas as pd

from solmu.core.batch import aggregate, run_matrix
from solmu.core.loader import load_commodities, load_network, load_policy, load_scenario

FIXTURES = Path(__file__).parent / "fixtures" / "tiny"
KEY_COLUMNS = ["scenario_id", "policy_id", "seed"]
METRICS = [
    "total_unmet",
    "population_days_in_shortage",
    "peak_edge_utilization",
    "holding_cost",
    "action_spend",
    "total_cost",
]


def _matrix() -> pd.DataFrame:
    base = load_policy(FIXTURES / "policy.json")
    policies = [base, base.model_copy(update={"id": "other"})]
    return run_matrix(
        load_network(FIXTURES),
        load_commodities(FIXTURES / "commodities.json"),
        load_scenario(FIXTURES / "scenario.json"),
        policies,
        [0, 1, 2],
    )


def test_matrix_shape_and_columns() -> None:
    df = _matrix()
    assert len(df) == 6
    assert list(df.columns[:3]) == KEY_COLUMNS
    assert set(METRICS) <= set(df.columns)
    assert any(c.startswith("unmet_") for c in df.columns)
    assert any(c.startswith("first_shortage_day_") for c in df.columns)


def test_matrix_is_deterministic() -> None:
    pd.testing.assert_frame_equal(_matrix(), _matrix())


def test_aggregate_one_row_per_policy() -> None:
    agg = aggregate(_matrix())
    assert len(agg) == 2
    assert {"total_unmet_mean", "total_unmet_min", "total_unmet_max"} <= set(agg.columns)
