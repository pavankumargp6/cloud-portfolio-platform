"""Restore verification backend abstraction.

Zero-budget: Simulates a restore by spinning up an in-memory SQLite DB,
executing a mock SQL dump, and querying it.
Production: Would download from S3 and run `pg_restore` against a temp DB.
"""

import logging
import sqlite3
import tempfile
import time
from typing import Protocol

logger = logging.getLogger(__name__)


class RestoreBackend(Protocol):
    def verify_restore(self, location: str) -> None:
        """Attempts to restore the backup.
        Raises an exception on failure.
        """
        ...


class SimulatedRestoreBackend:
    """Simulates a restore process locally using SQLite."""

    def verify_restore(self, location: str) -> None:
        # Simulate download/parsing time
        time.sleep(0.1)
        
        if "corrupt" in location:
            raise ValueError("Simulated error: Backup file is corrupt.")

        try:
            with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
                conn = sqlite3.connect(tmp.name)
                try:
                    # Create a dummy table and insert a row
                    conn.execute("CREATE TABLE test_restore (id INTEGER PRIMARY KEY, val TEXT)")
                    conn.execute("INSERT INTO test_restore (val) VALUES ('verify_ok')")
                    conn.commit()

                    # Verify we can read it back
                    cursor = conn.execute("SELECT val FROM test_restore")
                    row = cursor.fetchone()
                    if not row or row[0] != 'verify_ok':
                        raise RuntimeError("Restore verification failed: data mismatch")
                finally:
                    conn.close()
        except Exception as exc:
            logger.error("Simulated restore failed: %s", exc)
            raise


def get_backend() -> RestoreBackend:
    # In the future, this would check env vars for NOTIFICATION_BACKEND
    return SimulatedRestoreBackend()
