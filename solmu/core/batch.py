"""Run a scenario over a matrix of policies and seeds and collect summary rows."""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
from typing import Any

import pandas as pd

from solmu.core.engine import Simulation
from solmu.core.model import Commodities, Network, Policy, Scenario

KEY_COLUMNS = ["scenario_id", "policy_id", "seed"]
SCALAR_METRICS = [
    "population_days_in_shortage",
    "peak_edge_utilization",
    "holding_cost",
    "action_spend",
    "total_cost",
]


def flatten_summary(summary: dict[str, Any]) -> dict[str, Any]:
    """Flatten a `RunResult.summary` into one row of scalar columns."""
    row: dict[str, Any] = {"total_unmet": summary["total_unmet"]}
    for cid, value in summary["total_unmet_by_commodity"].items():
        row[f"unmet_{cid}"] = value
    for cid, day in summary["first_shortage_day"].items():
        row[f"first_shortage_day_{cid}"] = day
    for key in SCALAR_METRICS:
        row[key] = summary[key]
    return row


def run_one(
    network: Network,
    commodities: Commodities,
    scenario: Scenario,
    policy: Policy,
    seed: int,
) -> dict[str, Any]:
    """Run a single (policy, seed) simulation and return its flattened summary row."""
    result = Simulation(network, scenario, policy, commodities, seed).run()
    row = {"scenario_id": scenario.id, "policy_id": policy.id, "seed": seed}
    return {**row, **flatten_summary(result.summary)}


def run_matrix(
    network: Network,
    commodities: Commodities,
    scenario: Scenario,
    policies: list[Policy],
    seeds: list[int],
    *,
    workers: int = 1,
) -> pd.DataFrame:
    """Run every (policy, seed) pair independently; one summary row per pair."""
    jobs = [(p, s) for p in policies for s in seeds]
    if workers > 1:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(run_one, network, commodities, scenario, p, s) for p, s in jobs]
            rows = [f.result() for f in futures]
    else:
        rows = [run_one(network, commodities, scenario, p, s) for p, s in jobs]
    return pd.DataFrame(rows)


def aggregate(df: pd.DataFrame) -> pd.DataFrame:
    """Mean, min and max of every numeric metric per policy over seeds."""
    metrics = [c for c in df.columns if c not in KEY_COLUMNS]
    grouped = df.groupby("policy_id")[metrics].agg(["mean", "min", "max"])
    grouped.columns = [f"{metric}_{stat}" for metric, stat in grouped.columns]
    return grouped.reset_index()
