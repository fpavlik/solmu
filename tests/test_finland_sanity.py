"""The Finland dataset's required outcomes (spec 13.6, 13.9 and the refinery case) as a test.

`scripts/sanity_check.py` holds the checks; this makes `pytest` fail when the data drifts.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from sanity_check import evaluate  # noqa: E402


def test_finland_dataset_meets_every_required_outcome(capsys) -> None:
    checks = evaluate(ROOT / "solmu" / "data", "finland")
    capsys.readouterr()
    failed = [label for label, ok in checks.items() if not ok]
    assert not failed, f"failed sanity checks: {failed}"
    assert len(checks) == 8
