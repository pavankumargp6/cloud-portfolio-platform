"""Main worker loop and one-iteration function.

`run_once(db, probe_fn)` is the injectable seam used by tests. `run_forever`
installs signal handlers, retries DB on startup, and dispatches either a
probe or a prune each loop iteration.
"""

import logging
import signal
import time
from typing import Callable, Optional

from sqlalchemy import text

from app.db import SessionLocal
from app.monitoring.errors import SSRFError
from app.monitoring.probe import ProbeOutcome, probe as real_probe
from app.monitoring.retention import maybe_prune
from app.monitoring.scheduler import pick_due, record_outcome, reschedule
from app.services.incidents import handle_transition as handle_incidents

logger = logging.getLogger(__name__)

ProbeFn = Callable[[str], ProbeOutcome]
_shutdown = False


def _handle_signal(signum, frame):
    global _shutdown
    logger.info("received signal %s, shutting down", signum)
    _shutdown = True


def install_signal_handlers() -> None:
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)


def _default_on_transition(from_status: str, to_status: str) -> None:
    # M4 incident open/close will replace this in this module's caller.
    logger.info("app state transition %s -> %s", from_status, to_status)


def run_once(
    db,
    probe_fn: Optional[ProbeFn] = None,
    on_transition: Optional[Callable[[str, str], None]] = None,
) -> bool:
    """Pick one due app, probe it, record result, reschedule. Return True if worked."""
    probe_fn = probe_fn or real_probe
    on_transition = on_transition or _default_on_transition

    rows = pick_due(db, limit=1)
    if not rows:
        return False

    mon, app = rows[0]
    checked_at = None  # record_outcome/reschedule default to now

    try:
        outcome = probe_fn(app.base_url)
    except SSRFError as exc:
        logger.warning(
            "probe blocked SSRF for app %d (url=%s): %s",
            app.id, app.base_url, exc.code,
        )
        outcome = ProbeOutcome(
            status="blocked_ssrf",
            failure_reason=exc.code,
            final_url=app.base_url,
        )
    except Exception:
        logger.exception("probe failed unexpectedly for app %d", app.id)
        outcome = ProbeOutcome(
            status="down",
            failure_reason="worker_error",
            final_url=app.base_url,
        )

    # Define the transition hook. It has access to `db` and `app` via closure.
    def _transition_hook(from_status: str, to_status: str) -> None:
        handle_incidents(db, app.id, from_status, to_status, commit=False)
        if on_transition:
            on_transition(from_status, to_status)

    record_outcome(
        db, app.id, outcome, on_transition=_transition_hook, checked_at=checked_at
    )
    reschedule(db, app.id, checked_at=checked_at)
    db.commit()
    return True


def wait_for_db(timeout_sec: float = 30.0) -> None:
    start = time.monotonic()
    last_exc: Optional[Exception] = None
    while True:
        try:
            db = SessionLocal()
            try:
                db.execute(text("SELECT 1"))
            finally:
                db.close()
            return
        except Exception as exc:
            last_exc = exc
            if time.monotonic() - start > timeout_sec:
                raise RuntimeError(
                    f"database not reachable after {timeout_sec}s"
                ) from last_exc
            logger.info("waiting for database...")
            time.sleep(1)


def run_forever(
    loop_sleep_sec: float = 2.0,
    retention_days: int = 30,
    prune_interval_hours: int = 24,
) -> None:
    global _shutdown
    _shutdown = False
    install_signal_handlers()
    wait_for_db()
    logger.info(
        "worker ready (loop_sleep=%.1fs retention=%d days)",
        loop_sleep_sec, retention_days,
    )

    while not _shutdown:
        try:
            db = SessionLocal()
            try:
                worked = run_once(db)
            finally:
                db.close()

            if not worked:
                db = SessionLocal()
                try:
                    maybe_prune(db, retention_days, prune_interval_hours)
                finally:
                    db.close()
                time.sleep(loop_sleep_sec)
        except Exception:
            logger.exception("worker loop iteration failed")
            time.sleep(5)

    logger.info("worker stopped")
