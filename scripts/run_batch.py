"""Run a scenario over several policies and seeds and print aggregate metrics.

Usage: python scripts/run_batch.py --scenario hamina_winter_closure
    --policies finland_statutory,no_stockpiling --seeds 0-9
"""

import argparse
import sys
from pathlib import Path

import pandas as pd

from solmu.core.batch import aggregate, run_matrix
from solmu.core.loader import (
    LoadError,
    load_commodities,
    load_network,
    load_policy,
    load_scenario,
)


def parse_seeds(text: str) -> list[int]:
    """Parse `0-9`, `3` or `1,4,7` into a list of seeds."""
    seeds: list[int] = []
    for part in text.split(","):
        lo, sep, hi = part.partition("-")
        seeds.extend(range(int(lo), int(hi) + 1) if sep else [int(lo)])
    return seeds


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--policies", required=True, help="comma-separated policy ids")
    parser.add_argument("--seeds", default="0-9")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--country", default="finland")
    parser.add_argument("--data-dir", type=Path, default=Path("solmu/data"))
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    out = args.out or Path("results") / f"batch_{args.scenario}.csv"
    try:
        country = args.data_dir / args.country
        network = load_network(country)
        commodities = load_commodities(country / "commodities.json")
        scenario = load_scenario(args.data_dir / "scenarios" / f"{args.scenario}.json")
        policies = [
            load_policy(args.data_dir / "policies" / f"{pid}.json")
            for pid in args.policies.split(",")
        ]
        seeds = parse_seeds(args.seeds)
    except (LoadError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    df = run_matrix(network, commodities, scenario, policies, seeds, workers=args.workers)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    with pd.option_context("display.width", 200, "display.max_columns", None):
        print(aggregate(df).set_index("policy_id").T.to_string())
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
