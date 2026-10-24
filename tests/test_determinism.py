import pandas as pd
import pytest

from tests.helpers import chain_sim

EVENTS = [{"day": 2, "type": "node_capacity", "node_id": "src", "factor": 0.5, "until_day": 6}]


def _run(seed: int, noise: float):
    return chain_sim(EVENTS, horizon=15, seed=seed, noise=noise).run()


@pytest.mark.parametrize("noise", [0.0, 0.2])
def test_same_seed_gives_identical_tables(noise: float) -> None:
    a, b = _run(7, noise), _run(7, noise)
    pd.testing.assert_frame_equal(a.daily, b.daily)
    pd.testing.assert_frame_equal(a.edges_daily, b.edges_daily)
    assert a.summary == b.summary


def test_seed_is_irrelevant_without_noise() -> None:
    pd.testing.assert_frame_equal(_run(1, 0.0).daily, _run(2, 0.0).daily)


def test_different_seeds_differ_with_noise() -> None:
    a, b = _run(1, 0.2), _run(2, 0.2)
    assert not a.daily["demand"].equals(b.daily["demand"])
    demand = a.daily["demand"]
    assert demand.between(8.0, 12.0).all() and demand.nunique() > 1


def test_reset_replays_the_same_noise() -> None:
    sim = chain_sim(horizon=10, seed=3, noise=0.2)
    first = sim.run().daily
    sim.reset()
    pd.testing.assert_frame_equal(sim.run().daily, first)
