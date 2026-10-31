import logging

from app.worker.config import load_config
from app.worker.loop import run_forever


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    config = load_config()
    run_forever(
        loop_sleep_sec=config.loop_sleep_sec,
        retention_days=config.retention_days,
        prune_interval_hours=config.prune_interval_hours,
    )


if __name__ == "__main__":
    main()
