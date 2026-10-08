import asyncio
import logging
import os
import signal
from datetime import datetime, timezone

from .inference import MLPredictor

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s UTC [%(levelname)s] [neural] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("neural")

RUNNING = True


def handle_shutdown(signum, frame):
    global RUNNING
    logger.info("Shutdown signal received (%s). Exiting gracefully...", signum)
    RUNNING = False


signal.signal(signal.SIGINT, handle_shutdown)
signal.signal(signal.SIGTERM, handle_shutdown)


async def main():
    logger.info("Neural ML inference service initialized. Focus sport: %s", os.getenv("PRIMARY_SPORT", "tennis"))
    
    chk_dir = os.getenv("CHECKPOINTS_DIR", "/app/models/checkpoints")
    os.makedirs(chk_dir, exist_ok=True)
    available_ckpts = [f for f in os.listdir(chk_dir) if f.endswith(".joblib")]
    logger.info("Discovered %d model checkpoints in %s: %s", len(available_ckpts), chk_dir, available_ckpts[:5])

    with open("/tmp/healthy", "w") as f:
        f.write(datetime.now(timezone.utc).isoformat())

    while RUNNING:
        with open("/tmp/healthy", "w") as f:
            f.write(datetime.now(timezone.utc).isoformat())
        await asyncio.sleep(5)

    logger.info("Neural service stopped cleanly.")


if __name__ == "__main__":
    asyncio.run(main())
