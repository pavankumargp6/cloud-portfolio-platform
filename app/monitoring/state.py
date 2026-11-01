"""Pure health-state transition logic.

No DB, no network, no exceptions thrown by design. The worker calls
`apply_result` after every probe; the router calls `apply_threshold_change`
when PATCH updates the threshold. Both fire the optional `on_transition`
callback exactly once per state change — that's the M4 incident seam.

States: 'unknown' | 'up' | 'down'
Kinds:  'success' | 'failure' | 'blocked'
"""

from dataclasses import dataclass, replace
from typing import Callable, Optional

UNKNOWN = "unknown"
UP = "up"
DOWN = "down"

SUCCESS = "success"
FAILURE = "failure"
BLOCKED = "blocked"

TransitionCallback = Callable[[str, str], None]  # (from_status, to_status)


@dataclass(frozen=True)
class HealthState:
    status: str
    consecutive_failures: int


def outcome_to_kind(outcome_status: str) -> str:
    """Map a probe outcome status string to a transition kind.

    'up'           -> 'success'
    'blocked_ssrf' -> 'blocked'   (never moves the state)
    everything else (down, timeout, tls_error, dns_error, worker_error)
                   -> 'failure'
    """
    if outcome_status == "up":
        return SUCCESS
    if outcome_status == "blocked_ssrf":
        return BLOCKED
    return FAILURE


def apply_result(
    current: HealthState,
    outcome_kind: str,
    threshold: int,
    on_transition: Optional[TransitionCallback] = None,
) -> HealthState:
    """Apply one probe outcome to the current state.

    success -> up, cf=0
    failure -> cf+1; if cf >= threshold -> down, else state unchanged
    blocked -> state and cf unchanged
    """
    if outcome_kind == BLOCKED:
        return current

    if outcome_kind == SUCCESS:
        new_status = UP
        new_cf = 0
    elif outcome_kind == FAILURE:
        new_cf = current.consecutive_failures + 1
        if new_cf >= threshold:
            new_status = DOWN
        else:
            new_status = current.status
    else:
        raise ValueError(f"unknown outcome_kind: {outcome_kind!r}")

    if new_status != current.status and on_transition is not None:
        on_transition(current.status, new_status)

    return replace(current, status=new_status, consecutive_failures=new_cf)


def apply_threshold_change(
    current: HealthState,
    new_threshold: int,
    on_transition: Optional[TransitionCallback] = None,
) -> HealthState:
    """Re-evaluate the current state against a new failure threshold.

    If the existing counter already meets the new threshold and we are not
    already 'down', transition to 'down'. Never auto-recovers — recovery
    requires a success, not a threshold change.
    """
    if current.status != DOWN and current.consecutive_failures >= new_threshold:
        if on_transition is not None:
            on_transition(current.status, DOWN)
        return replace(current, status=DOWN)
    return current
