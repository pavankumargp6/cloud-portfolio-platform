import os
from dataclasses import dataclass


@dataclass(frozen=True)
class WorkerConfig:
    loop_sleep_sec: float
    retention_days: int
    prune_interval_hours: int


def load_config() -> WorkerConfig:
    return WorkerConfig(
        loop_sleep_sec=float(os.getenv("MONITORING_LOOP_SLEEP_SEC", "2")),
        retention_days=int(os.getenv("MONITORING_RETENTION_DAYS", "30")),
        prune_interval_hours=int(os.getenv("MONITORING_PRUNE_INTERVAL_HOURS", "24")),
    )
