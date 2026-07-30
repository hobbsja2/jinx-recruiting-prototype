"""Shared residency-aware tuition logic.

Lives in its own module so both the web UI (main.py) and the PDF export
(reports.py) apply identical rules without a circular import.
"""
from __future__ import annotations

from .models import College, Player


def norm_state(value: str | None) -> str:
    """Normalize a state value for comparison (upper-case, trimmed)."""
    return (value or "").strip().upper()


def is_out_of_state(player: Player | None, college: College) -> bool:
    """True when we know the player's home state and it differs from the college's.

    Requires both states to be present; an unknown player state never triggers
    the out-of-state rate (we don't assume residency)."""
    if player is None:
        return False
    ps, cs = norm_state(player.home_state), norm_state(college.state)
    return bool(ps and cs and ps != cs)


def tuition_for(college: College, player: Player | None) -> tuple[float | None, bool]:
    """Return (amount, is_oos_and_higher) for the tuition to display.

    Combined figure = applicable tuition (in-state vs out-of-state) + housing.
    The out-of-state flag is only set when the player is out-of-state AND the
    out-of-state total actually exceeds the in-state total — private schools charge
    one rate to everyone, so their out-of-state == in-state and we don't flag them."""
    housing = college.housing_cost or 0
    if is_out_of_state(player, college) and college.out_of_state_tuition is not None:
        oos_total = college.out_of_state_tuition + housing
        in_state_total = college.tuition
        if in_state_total is None or oos_total > in_state_total:
            return oos_total, True
        # Out-of-state rate matches in-state (private single rate): no distinction.
        return in_state_total, False
    return college.tuition, False
