"""Import redirection between ports: shortfalls divert to ports with spare handling capacity."""

from __future__ import annotations

from collections.abc import Mapping

from solmu.core.model import NodeId

DUST = 1e-9  # tonnes


def redirect_step(
    day: int,
    delay_days: int,
    baseline: Mapping[NodeId, float],
    direct: Mapping[NodeId, float],
    cap: Mapping[NodeId, float],
    ready: float,
    pending: list[tuple[int, float]],
    loss_fraction: float = 0.0,
) -> tuple[dict[NodeId, float], float, list[tuple[int, float]], float]:
    """One day of redirection for one commodity.

    `baseline`, `direct` (what each port can handle of its own baseline) and `cap` (effective
    handling cap) are tonnes/day per port. Today's shortfall (baseline - direct) enters
    `pending` and becomes available on `day + delay_days`; the available pool plus `ready`
    backlog is spread over ports in proportion to spare capacity (cap - direct), capped by it.
    Then `loss_fraction` of the remaining available backlog is lost (cargo leaves for other
    markets); pending entries are not reduced. Returns (offer per port, new ready backlog,
    new pending list, lost tonnes).
    """
    shortfall = sum(max(0.0, baseline[p] - direct[p]) for p in baseline)
    queue = [*pending, (day + delay_days, shortfall)] if shortfall > DUST else list(pending)
    available = ready + sum(t for d, t in queue if d <= day)
    queue = [(d, t) for d, t in queue if d > day]
    spare = {p: max(0.0, cap[p] - direct[p]) for p in baseline}
    total_spare = sum(spare.values())
    offers = dict(direct)
    if available > DUST and total_spare > DUST:
        moved = min(available, total_spare)
        for p, room in spare.items():
            offers[p] += moved * room / total_spare
        available -= moved
    lost = available * loss_fraction
    available -= lost
    return offers, (available if available > DUST else 0.0), queue, lost
