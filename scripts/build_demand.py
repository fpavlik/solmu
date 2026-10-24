"""Generate demand.csv from region populations and per-capita rates.

Usage: python scripts/build_demand.py [--data-dir solmu/data/finland]
"""

import argparse
import csv
import json
from pathlib import Path


def build_rows(data_dir: Path) -> list[tuple[str, str, float]]:
    """Return (node_id, commodity_id, tonnes_per_day) for every region node."""
    with (data_dir / "commodities.json").open(encoding="utf-8") as fh:
        commodities = json.load(fh)["commodities"]
    rows = []
    with (data_dir / "nodes.csv").open(encoding="utf-8", newline="") as fh:
        for node in csv.DictReader(fh):
            if node["kind"] != "region":
                continue
            population = int(node["population"])
            for c in commodities:
                tonnes = population * float(c["per_capita_kg_per_day"]) / 1000
                rows.append((node["id"], c["id"], round(tonnes, 1)))
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("solmu/data/finland"))
    args = parser.parse_args()
    rows = build_rows(args.data_dir)
    out = args.data_dir / "demand.csv"
    with out.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(["node_id", "commodity_id", "tonnes_per_day"])
        writer.writerows(rows)
    print(f"wrote {len(rows)} rows to {out}")


if __name__ == "__main__":
    main()
