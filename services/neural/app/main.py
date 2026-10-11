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

    # Start embedded metrics HTTP server for Prometheus scraping on port 8000
    try:
        from http.server import HTTPServer, BaseHTTPRequestHandler
        import threading

        class NeuralMetricsHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/health":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"status":"healthy","service":"neural"}')
                elif self.path == "/metrics":
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
                    self.end_headers()
                    output = (
                        f"# HELP neural_up Service status\n"
                        f"# TYPE neural_up gauge\n"
                        f"neural_up 1\n"
                        f"# HELP neural_checkpoints_count Count of local checkpoints\n"
                        f"# TYPE neural_checkpoints_count gauge\n"
                        f"neural_checkpoints_count {len(available_ckpts)}\n"
                    )
                    self.wfile.write(output.encode("utf-8"))
                else:
                    self.send_response(404)
                    self.end_headers()

            def log_message(self, format, *args):
                pass  # suppress access logs to keep terminal quiet

        httpd = HTTPServer(("0.0.0.0", 8000), NeuralMetricsHandler)
        server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        server_thread.start()
        logger.info("Embedded neural HTTP metrics server started on port 8000 (/metrics, /health)")
    except Exception as e:
        logger.warning("Could not start embedded metrics server: %s", e)

    with open("/tmp/healthy", "w") as f:
        f.write(datetime.now(timezone.utc).isoformat())

    while RUNNING:
        with open("/tmp/healthy", "w") as f:
            f.write(datetime.now(timezone.utc).isoformat())
        await asyncio.sleep(5)

    logger.info("Neural service stopped cleanly.")


if __name__ == "__main__":
    asyncio.run(main())
