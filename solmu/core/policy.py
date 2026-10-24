"""Policy rules applied each tick: release of strategic stock into usable stock."""

from __future__ import annotations

from collections.abc import Mapping

from solmu.core.model import CommodityId, ReleaseRule
from solmu.core.state import Key


def disruption_step(
    rule: ReleaseRule,
    day: int,
    drop: float,
    streak: int,
    started: int | None,
    prev_unmet: bool,
) -> tuple[int, int | None, bool]:
    """One day of the `on_disruption` trigger for one commodity: (streak, started, releasing).

    `streak` counts consecutive days with `drop` above the threshold. When it reaches
    `disruption_persistence_days` the national countdown starts (`started = day`, once per run;
    it is never reset). From `started + delay_days` on, release is on while `drop` is above the
    threshold or any region had unmet demand of the commodity the previous day (`prev_unmet`),
    and pauses otherwise; a later disruption resumes it at once.
    """
    above = drop > rule.disruption_threshold
    streak = streak + 1 if above else 0
    if started is None and streak >= rule.disruption_persistence_days:
        started = day
    releasing = started is not None and day >= started + rule.delay_days and (above or prev_unmet)
    return streak, started, releasing


def release_step(
    rule: ReleaseRule,
    day: int,
    strategic: Mapping[Key, float],
    stocks: Mapping[Key, float],
    demand: Mapping[Key, float],
    prev_unmet: Mapping[Key, float],
    started: dict[Key, int],
    disruption_releasing: Mapping[CommodityId, bool] | None = None,
) -> dict[Key, float]:
    """Tonnes of strategic stock to release today per key; updates `started` in place.

    `always`: release the max fraction every day. `manual`: never. `shortage`: the clock
    for a key starts on the first day whose previous day had unmet demand; from
    `started + delay_days` on, release the max fraction on days when usable stock (after
    arrivals) is below today's demand. The clock is never reset within a run.
    `on_disruption`: release the max fraction for every commodity flagged in
    `disruption_releasing` (see `disruption_step`).
    """
    released: dict[Key, float] = {}
    if rule.trigger == "manual":
        return released
    for key, held in strategic.items():
        if held <= 0:
            continue
        if rule.trigger == "shortage":
            if key not in started and prev_unmet.get(key, 0.0) > 0:
                started[key] = day
            if key not in started or day < started[key] + rule.delay_days:
                continue
            if stocks[key] >= demand[key]:
                continue
        if rule.trigger == "on_disruption" and not (disruption_releasing or {}).get(key[1]):
            continue
        released[key] = rule.max_release_fraction_per_day * held
    return released
