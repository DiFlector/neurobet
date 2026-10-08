import asyncio
import logging
import os
import signal
from datetime import datetime, timezone

from sports_core import registry
# Import tennis adapter to auto-register it in global sports registry
try:
    import tennis_adapter
except ImportError:
    pass

from .validator import BetValidator

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s UTC [%(levelname)s] [bet-manager] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("bet-manager")

RUNNING = True


def handle_shutdown(signum, frame):
    global RUNNING
    logger.info("Shutdown signal received (%s). Exiting gracefully...", signum)
    RUNNING = False


signal.signal(signal.SIGINT, handle_shutdown)
signal.signal(signal.SIGTERM, handle_shutdown)


async def main():
    logger.info("Bet-Manager service started. Simulation Mode: %s", os.getenv("BET_MODE", "SIMULATION"))
    logger.info("Initial Virtual Bankroll: %s", os.getenv("INITIAL_BANKROLL", "100000.00"))
    
    validator = BetValidator(
        max_stake=float(os.getenv("MAX_STAKE", "10000.0")),
        min_odds=float(os.getenv("MIN_ODDS", "1.05")),
        max_odds=float(os.getenv("MAX_ODDS", "50.0")),
    )
    logger.info("Loaded BetValidator. Supported sports: %s", registry.list_sports())

    with open("/tmp/healthy", "w") as f:
        f.write(datetime.now(timezone.utc).isoformat())

    while RUNNING:
        # Update heartbeat
        with open("/tmp/healthy", "w") as f:
            f.write(datetime.now(timezone.utc).isoformat())
        await asyncio.sleep(5)

    logger.info("Bet-Manager stopped cleanly.")


if __name__ == "__main__":
    asyncio.run(main())
