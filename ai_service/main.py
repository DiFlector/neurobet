import logging
import os
import sys
from pathlib import Path

# Docker copies neurobet_filters onto /app; locally it's under repo/shared.
_shared = Path(__file__).resolve().parent.parent / "shared"
if (_shared / "neurobet_filters").is_dir() and str(_shared) not in sys.path:
    sys.path.insert(0, str(_shared))

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.neuralbet import add_ai_log, run_backtest

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s: %(message)s")
logger = logging.getLogger("ai_service_main")

app = FastAPI(title="NeuroBet AI Microservice (PyTorch & LightGBM)", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)

from neurobet_time import MOSCOW_TZ

# Same default the admin panel's manual "Запустить бэктест" button uses (see
# frontend/autobet/app/admin/page.tsx) — the automatic runs and manual ones should
# cover a comparably representative slice of the archive unless deliberately overridden.
SCHEDULED_BACKTEST_LIMIT = int(os.getenv("NEURALBET_SCHEDULED_BACKTEST_LIMIT", "80000"))
# Cron fields (Moscow). Default: every 3 hours on the hour.
SCHEDULED_BACKTEST_CRON_MINUTES = os.getenv(
    "NEURALBET_SCHEDULED_BACKTEST_CRON_MINUTES", "0"
).strip() or "0"
SCHEDULED_BACKTEST_CRON_HOURS = os.getenv(
    "NEURALBET_SCHEDULED_BACKTEST_CRON_HOURS", "*/3"
).strip() or "*/3"

scheduler = BackgroundScheduler(timezone=MOSCOW_TZ)


def run_scheduled_backtest():
    """
    Fires on the cron below (default every 3h at :00 Moscow) via the job in
    startup_event. Calls run_backtest() directly in-process rather than over HTTP —
    the admin panel's manual button has to go through backend's proxy
    (browser -> backend -> ai_service), which is a real request with a timeout on both
    hops; a scheduled job running inside the same process as the model it's evaluating
    has no such round trip and therefore no such timeout to spuriously hit while it
    legitimately waits its turn for pipeline._engine_lock behind a training cycle
    (see run_backtest's docstring — the lock now covers the backtest's entire scoring
    pass, not just the forward pass, so training and backtest can never interleave,
    only queue behind each other).
    """
    try:
        result = run_backtest(limit=SCHEDULED_BACKTEST_LIMIT)
        if result.get("status") == "success":
            overall = result.get("overall") or {}
            current = overall.get("current") or {}
            add_ai_log(
                "SYSTEM",
                f"Scheduled backtest: {result['samples_evaluated']} samples in "
                f"{result['duration_seconds']:.1f}s — accuracy {current.get('accuracy_pct')}%, "
                f"{current.get('bets')} bets, ROI {current.get('roi_pct')}%, "
                f"Brier {current.get('brier')} (market {overall.get('market_brier')}).",
            )
        elif result.get("status") == "skipped_cold_start":
            pass
        else:
            add_ai_log(
                "SYSTEM",
                f"Scheduled backtest skipped: {result.get('status')} — not enough data.",
                level="WARNING",
            )
    except Exception as e:
        logger.error(f"Scheduled backtest failed: {e}", exc_info=True)
        add_ai_log("SYSTEM", f"Scheduled backtest failed: {e}", level="WARNING")


@app.on_event("startup")
def startup_event():
    from app.config import IS_DEV

    if IS_DEV:
        scheduler.add_job(
            run_scheduled_backtest,
            CronTrigger(
                hour=SCHEDULED_BACKTEST_CRON_HOURS,
                minute=SCHEDULED_BACKTEST_CRON_MINUTES,
                timezone=MOSCOW_TZ,
            ),
            id="scheduled_backtest",
            misfire_grace_time=1800,
            max_instances=1,
            coalesce=True,
        )
        scheduler.start()
        logger.info(
            "Scheduler started! Backtest will run automatically every 3h "
            f"(cron hour={SCHEDULED_BACKTEST_CRON_HOURS!r} minute={SCHEDULED_BACKTEST_CRON_MINUTES!r} "
            "Moscow time)."
        )
    else:
        logger.info("Prod deploy mode — scheduled backtest disabled.")
    add_ai_log(
        "SYSTEM",
        "AI worker ready — inference/training cycles run in background threads.",
    )
    try:
        from app.neuralbet.backtest import get_latest_backtest
        from neurobet_filters import update_brier_stake_sports_from_backtest

        latest = get_latest_backtest()
        if latest:
            payload = update_brier_stake_sports_from_backtest(latest)
            names = ", ".join(payload.get("sports") or []) or "none"
            add_ai_log(
                "SYSTEM",
                f"Brier sport gate from latest backtest ({payload.get('source')}): {names}.",
            )
    except Exception as e:
        logger.warning(f"Brier sport gate bootstrap failed: {e}")


@app.on_event("shutdown")
def shutdown_event():
    logger.info("Shutting down scheduler...")
    if scheduler.running:
        scheduler.shutdown(wait=False)
