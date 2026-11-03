"""Prune old check_results. Batched, throttled, in-memory timestamp.

The prune is deliberately not aggressive — one DELETE per 10k rows in a
fresh transaction, so the worker is not blocked for seconds at a time and
the check_results reader path (dashboard, API) is not locked.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_last_prune_at: Optional[datetime] = None


def _reset_prune_state() -> None:
    """Test hook — clears the throttle."""
    global _last_prune_at
    _last_prune_at = None


def maybe_prune(
    db: Session,
    retention_days: int = 30,
    prune_interval_hours: int = 24,
    batch_size: int = 10000,
) -> int:
    """Delete check_results older than retention_days. Throttled. Returns count."""
    global _last_prune_at

    now = datetime.now(timezone.utc)
    if _last_prune_at is not None:
        if now - _last_prune_at < timedelta(hours=prune_interval_hours):
            return 0

    total = 0
    while True:
        result = db.execute(
            text(
                "DELETE FROM check_results WHERE id IN ("
                "  SELECT id FROM check_results "
                "  WHERE checked_at < NOW() - make_interval(days => :days) "
                "  LIMIT :batch"
                ")"
            ),
            {"days": retention_days, "batch": batch_size},
        )
        db.commit()
        n = result.rowcount
        total += n
        if n < batch_size:
            break

    if total > 0:
        logger.info(
            "pruned %d check_results (retention=%d days)", total, retention_days
        )
    _last_prune_at = now
    return total
