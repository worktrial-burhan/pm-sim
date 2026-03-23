from __future__ import annotations

import logging

from app.services.temporal_service import run_temporal_worker_sync, temporal_enabled
from app.services.tick_loop import TickLoop


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if temporal_enabled():
        run_temporal_worker_sync()
        return

    TickLoop().serve_forever()


if __name__ == "__main__":
    main()
