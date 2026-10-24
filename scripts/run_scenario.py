"""Run one scenario with one policy and write the result as parquet.

Usage: python scripts/run_scenario.py --scenario baseline --policy no_stockpiling --seed 0
"""

import argparse
import sys
import time
from pathlib import Path
from typing import Any

from solmu.core.engine import Simulation
from solmu.core.loader import (
    LoadError,
    load_commodities,
    load_network,
    load_policy,
    load_scenario,
)


def format_summary(summary: dict[str, Any], elapsed: float) -> str:
    """Render a run summary as a compact text table."""
    first = summary["first_shortage_day"]
    lines = [f"{'commodity':<16}{'unmet_t':>14}{'first_short_day':>18}"]
    for cid, unmet in summary["total_unmet_by_commodity"].items():
        day = first.get(cid)
        lines.append(f"{cid:<16}{unmet:>14,.1f}{'-' if day is None else day:>18}")
    lines.append(f"{'total':<16}{summary['total_unmet']:>14,.1f}")
    lines.append(
        f"holding_cost={summary['holding_cost']:,.1f}  "
        f"action_spend={summary['action_spend']:,.1f}  "
        f"total_cost={summary['total_cost']:,.1f}"
    )
    lines.append(
        f"population_days_in_shortage={summary['population_days_in_shortage']:,.0f}  "
        f"peak_edge_utilization={summary['peak_edge_utilization']:.2f}"
    )
    lines.append(f"run time: {elapsed:.2f}s")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--policy", required=True)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--country", default="finland")
    parser.add_argument("--data-dir", type=Path, default=Path("solmu/data"))
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    out = args.out or Path("results") / f"{args.scenario}_{args.policy}_{args.seed}"
    try:
        country = args.data_dir / args.country
        network = load_network(country)
        commodities = load_commodities(country / "commodities.json")
        scenario = load_scenario(args.data_dir / "scenarios" / f"{args.scenario}.json")
        policy = load_policy(args.data_dir / "policies" / f"{args.policy}.json")
    except LoadError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    start = time.perf_counter()
    result = Simulation(network, scenario, policy, commodities, args.seed).run()
    elapsed = time.perf_counter() - start
    result.to_parquet(out)
    print(format_summary(result.summary, elapsed))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
