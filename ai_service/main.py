import logging
import os
import sys
from datetime import timedelta, timezone
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

app = FastAPI(title="NeuroBet AI Microservice (PyTorch, LightGBM & DeepSeek WASM)", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)

# Fixed +3 offset — same MOSCOW_TZ every other module in this service already uses
# (pipeline.py, backtest.py), not a tzdata-based zone name, so this needs no extra
# timezone-database dependency.
MOSCOW_TZ = timezone(timedelta(hours=3))

# Same default the admin panel's manual "Запустить бэктест" button uses (see
# frontend/autobet/app/admin/page.tsx) — the automatic runs and manual ones should
# cover a comparably representative slice of the archive unless deliberately overridden.
SCHEDULED_BACKTEST_LIMIT = int(os.getenv("NEURALBET_SCHEDULED_BACKTEST_LIMIT", "80000"))
# Cron minute field (Moscow). Default every half hour on the clock (:00 and :30).
SCHEDULED_BACKTEST_CRON_MINUTES = os.getenv(
    "NEURALBET_SCHEDULED_BACKTEST_CRON_MINUTES", "0,30"
).strip() or "0,30"

scheduler = BackgroundScheduler(timezone=MOSCOW_TZ)


def run_scheduled_backtest():
    """
    Fires on the cron minutes below (default :00 and :30 Moscow) via the job in
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
                f"Плановый бэктест: {result['samples_evaluated']} сэмплов за "
                f"{result['duration_seconds']:.1f}с — точность {current.get('accuracy_pct')}%, "
                f"{current.get('bets')} ставок, ROI {current.get('roi_pct')}%, "
                f"Brier {current.get('brier')} (рынок {overall.get('market_brier')}).",
            )
        else:
            add_ai_log(
                "SYSTEM",
                f"Плановый бэктест пропущен: {result.get('status')} — недостаточно данных.",
                level="WARNING",
            )
    except Exception as e:
        logger.error(f"Scheduled backtest failed: {e}", exc_info=True)
        add_ai_log("SYSTEM", f"Плановый бэктест завершился ошибкой: {e}", level="WARNING")


@app.on_event("startup")
def startup_event():
    scheduler.add_job(
        run_scheduled_backtest,
        CronTrigger(minute=SCHEDULED_BACKTEST_CRON_MINUTES, timezone=MOSCOW_TZ),
        id="scheduled_backtest",
        misfire_grace_time=1800,
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    logger.info(
        "Scheduler started! Backtest will run automatically every 30 min "
        f"(cron minutes={SCHEDULED_BACKTEST_CRON_MINUTES!r} Moscow time)."
    )
    add_ai_log(
        "SYSTEM",
        "AI worker ready — inference/training cycles run in background threads.",
    )


@app.on_event("shutdown")
def shutdown_event():
    logger.info("Shutting down scheduler...")
    if scheduler.running:
        scheduler.shutdown(wait=False)
