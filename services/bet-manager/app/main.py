import asyncio
import logging
import os
import signal
from datetime import datetime, timezone

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
