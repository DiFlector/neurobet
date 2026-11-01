"""Reserve one shared browser slot and retain Flashscore feed headers in that browser."""
import fcntl
from functools import partial
import os
from pathlib import Path
import runpy
import sys
import time
from urllib.parse import urlsplit


def feed_headers(url):
    parsed = urlsplit(url)
    if (parsed.scheme == "https" and parsed.hostname == "global.flashscore.ninja" and
            parsed.path.startswith("/2/x/feed/")):
        return {"x-fsign": os.getenv("FLASHSCORE_X_FSIGN", "SW9D1eZo"),
                "Referer": "https://www.flashscore.com/tennis/"}
    return {}


def request_with_browser_metadata(original, req, driver, method):
    driver.execute_cdp_cmd("Network.enable", {})
    driver.execute_cdp_cmd("Network.setExtraHTTPHeaders", {"headers": feed_headers(req.url or "")})
    try:
        result = original(req, driver, method)
        # Upstream hardcodes solution.status=200. Keep API status="ok" and
        # replace only the final document's HTTP status. Unknown fails closed.
        status = driver.execute_script(
            "return performance.getEntriesByType('navigation')[0]?.responseStatus || 0")
        result.result.status = status if isinstance(status, int) and 100 <= status < 600 else 0
        return result
    finally:
        driver.execute_cdp_cmd("Network.setExtraHTTPHeaders", {"headers": {}})


def run_solver():
    slots = Path(os.getenv("FLASHSCORE_BROWSER_SLOT_DIR", "/run/flashscore-browser-slots"))
    slots.mkdir(parents=True, exist_ok=True)
    limit = max(1, min(4, int(os.getenv("FLASHSCORE_BROWSER_PROCESSES", "2"))))
    while True:
        for index in reversed(range(limit)):
            fd = os.open(slots / f"browser-{index}.lock", os.O_CREAT | os.O_RDWR, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                os.close(fd)
                continue
            # This process keeps the slot until exit, including idle sessions.
            sys.path.insert(0, "/app")
            import flaresolverr_service
            original = flaresolverr_service._evil_logic

            flaresolverr_service._evil_logic = partial(request_with_browser_metadata, original)
            sys.argv[0] = "/app/flaresolverr.py"
            runpy.run_path("/app/flaresolverr.py", run_name="__main__")
            return
        time.sleep(0.5)


if __name__ == "__main__":
    run_solver()
