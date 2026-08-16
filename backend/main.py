import logging
import datetime
import os
import threading
import httpx
from fastapi import FastAPI, Query, HTTPException, BackgroundTasks, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from typing import Optional, List, Dict, Any
from pydantic import BaseModel

from database import init_db, save_parsed_events, get_live_matches, get_odds_history, get_db_stats, get_top_neurobets, get_neurobets_history, get_bet_type_stats, get_roi_stats, reset_live_database, reset_all_databases, get_bankroll_state, get_live_bets, get_live_account, place_live_bet_candidates, reset_live_account, cancel_open_live_bets
from parser_service import FonbetParserService
from settings import settings

MOSCOW_TZ = datetime.timezone(datetime.timedelta(hours=3))

def now_moscow() -> datetime.datetime:
    return datetime.datetime.now(MOSCOW_TZ)

class AdminLoginRequest(BaseModel):
    username: str
    password: str

class AISettingsRequest(BaseModel):
    ai_enabled: Optional[bool] = None
    training_enabled: Optional[bool] = None

class BankrollResetRequest(BaseModel):
    account: str
    start_balance: Optional[float] = None

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s: %(message)s")
logger = logging.getLogger("backend_main")

app = FastAPI(title=settings.APP_NAME, version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

parser_service = FonbetParserService()
scheduler = AsyncIOScheduler()

AI_SERVICE_URL = os.getenv("AI_SERVICE_URL", "http://ai:8001")

# Guards against piling up overlapping /predict-and-train calls. ai_service is a single
# Uvicorn worker running synchronous CPU-bound torch training inside the request handler
# — while one call is in flight, the process can't even accept a new connection promptly,
# so firing a fresh thread every 15s regardless of whether the last one finished just
# grows an ever-longer backlog of doomed requests (observed: they pile up and eventually
# time out at the 120s mark instead of ever getting served). Skipping the trigger
# whenever one is already running keeps AI throughput matched to what it can actually
# sustain, independent of the scrape loop's own fixed cadence.
_ai_trigger_lock = threading.Lock()
_ai_trigger_in_flight = False

def trigger_ai_pipeline(scrape_timestamp: str):
    # Pinning the exact timestamp of the scrape cycle that just committed (rather than
    # letting ai_service just query "whatever's freshest" when its own request happens
    # to run) is what makes "AI only ever bets on the data from the scrape that just
    # finished" a hard guarantee instead of something that's merely usually true.
    #
    # Fire-and-forget: run_scrape_task (below) is a single APScheduler job with
    # max_instances=1 — if this call blocked here waiting on ai_service's response, a
    # training pass slower than the scrape interval would silently eat the *next*
    # scheduled scrape entirely, not just delay it. Now that scraping runs every 15s
    # (down from 60s) and training got room to actually use the hardware (see
    # ai_service/app/neuralbet/{model,pipeline}.py), that coupling would otherwise cap
    # how much training each cycle could afford. Detaching this onto its own thread lets
    # ai_service take as long as it needs without ever holding up the scrape loop.
    global _ai_trigger_in_flight
    with _ai_trigger_lock:
        if _ai_trigger_in_flight:
            logger.info("Skipping AI trigger this cycle — previous training pass still in flight.")
            return
        _ai_trigger_in_flight = True

    def _fire():
        global _ai_trigger_in_flight
        try:
            with httpx.Client(timeout=120.0) as client:
                res = client.post(f"{AI_SERVICE_URL}/predict-and-train", json={"scrape_timestamp": scrape_timestamp})
                if res.status_code == 200:
                    logger.info(f"AI Service trigger response: {res.json()}")
        except Exception as e:
            logger.error(f"Failed to communicate with AI Service container at {AI_SERVICE_URL}: {e}")
        finally:
            with _ai_trigger_lock:
                _ai_trigger_in_flight = False

    threading.Thread(target=_fire, daemon=True).start()

def push_ai_logs(messages: List[Dict[str, str]]):
    """Forwards bet placement/settlement narration to ai_service's log stream (shown on
    the admin "Нейростав" page) — backend owns the live_bets data now, but ai_service
    still owns the visible log feed, so it needs to hear about what backend just did."""
    if not messages:
        return
    try:
        with httpx.Client(timeout=10.0) as client:
            client.post(f"{AI_SERVICE_URL}/internal/logs", json={"logs": messages})
    except Exception as e:
        logger.error(f"Failed to push bankroll logs to AI Service at {AI_SERVICE_URL}: {e}")

def run_scrape_task():
    logger.info("Executing background Fonbet LIVE scrape task...")
    try:
        parsed_events, seen_live_ids = parser_service.parse_live()
        now_str = now_moscow().strftime("%Y-%m-%d %H:%M:%S")

        if not seen_live_ids:
            # Empty snapshot almost always means a parser/CDN hiccup, not "every
            # live match ended at once" — skip the whole save/finalize cycle so we
            # don't archive the entire live database.
            logger.warning("Empty LIVE snapshot from Fonbet — skipping save/finalize cycle.")
            return

        settle_result = save_parsed_events(parsed_events, now_str, present_event_ids=seen_live_ids)
        logger.info(f"Successfully scraped and stored {len(parsed_events)} events at {now_str}")
        push_ai_logs(settle_result.get("messages", []))

        # Trigger AI Service Microservice Container (Port 8001)
        trigger_ai_pipeline(now_str)
    except Exception as e:
        logger.error(f"Error during scheduled scrape: {e}", exc_info=True)

@app.on_event("startup")
def startup_event():
    logger.info("Initializing Database...")
    init_db()

    # Schedule recurring task based on settings
    interval = settings.SCRAPE_INTERVAL_SECONDS
    scheduler.add_job(run_scrape_task, 'interval', seconds=interval, id="fonbet_scraper")
    scheduler.start()
    logger.info(f"Scheduler started! Fonbet LIVE matches will be scraped every {interval} seconds.")
    
    # Run initial scrape in background job so port 8000 opens instantly
    scheduler.add_job(run_scrape_task, id="initial_scrape_job")

@app.on_event("shutdown")
def shutdown_event():
    logger.info("Shutting down scheduler...")
    if scheduler.running:
        scheduler.shutdown()

@app.get("/api/matches")
def read_matches(
    sport: Optional[str] = Query(None, description="Filter by sport path (e.g. Футбол, Баскетбол)"),
    search: Optional[str] = Query(None, description="Search by team or match name")
):
    try:
        matches = get_live_matches(sport_filter=sport, search=search)
        # Returning a JSONResponse directly skips FastAPI's default jsonable_encoder
        # pass, which recursively re-validates every value with isinstance checks — for
        # this payload's ~250k nested odds rows that pass alone took minutes. The data
        # is already plain str/int/float/bool/None from psycopg2's RealDictCursor, so plain json.dumps
        # (which JSONResponse uses) is sufficient and orders of magnitude faster.
        return JSONResponse(content={
            "status": "success",
            "count": len(matches),
            "matches": matches
        })
    except Exception as e:
        logger.error(f"Error fetching matches: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/matches/{event_id}/odds-history")
def read_odds_history(
    event_id: int,
    factor_id: int = Query(..., description="Factor ID (e.g. 921 for П1, 930 for Total Over)"),
    parameter: Optional[str] = Query(None, description="Line parameter (e.g. 2.5)"),
    market_prefix: Optional[str] = Query(None, description="Market prefix (e.g. Основной матч)")
):
    try:
        history = get_odds_history(
            event_id=event_id,
            factor_id=factor_id,
            parameter=parameter,
            market_prefix=market_prefix
        )
        return {
            "status": "success",
            "event_id": event_id,
            "factor_id": factor_id,
            "parameter": parameter,
            "count": len(history),
            "history": history
        }
    except Exception as e:
        logger.error(f"Error fetching odds history: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/stats")
def read_stats():
    try:
        stats = get_db_stats()
        return {
            "status": "success",
            "stats": stats
        }
    except Exception as e:
        logger.error(f"Error fetching stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/neurobets/top")
def read_neurobets_top(
    sport: Optional[str] = Query(None, description="Filter by sport path"),
    sort: str = Query("best", description="Sort mode: 'best' or 'safe'"),
    limit: int = Query(50, description="Items limit"),
    offset: int = Query(0, description="Items offset"),
    verdict: str = Query("win", description="Filter by model verdict: 'win', 'loss', or 'all'"),
    search: Optional[str] = Query(None, description="Search by team, match, or bet type"),
):
    try:
        res = get_top_neurobets(
            sport_filter=sport,
            sort_mode=sort,
            limit=limit,
            offset=offset,
            verdict=verdict,
            search=search,
        )
        return {
            "status": "success",
            "count": len(res["items"]),
            "total": res["total"],
            "bets": res["items"]
        }
    except Exception as e:
        logger.error(f"Error fetching neurobets: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/neurobets/stats-by-type")
def read_bet_type_stats():
    try:
        res = get_bet_type_stats()
        return {"status": "success", **res}
    except Exception as e:
        logger.error(f"Error fetching bet-type stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/neurobets/roi-stats")
def read_roi_stats():
    try:
        res = get_roi_stats()
        return {"status": "success", **res}
    except Exception as e:
        logger.error(f"Error fetching ROI stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/neurobets/history")
def read_neurobets_history(
    sport: Optional[str] = Query(None, description="Filter by sport path"),
    search: Optional[str] = Query(None, description="Search by team or match name"),
    outcome: Optional[str] = Query(None, description="Filter by outcome: correct, incorrect, push, or pending"),
    limit: int = Query(50, description="Items limit"),
    offset: int = Query(0, description="Items offset")
):
    try:
        res = get_neurobets_history(
            sport_filter=sport,
            search=search,
            outcome_filter=outcome,
            limit=limit,
            offset=offset
        )
        return {
            "status": "success",
            "summary": res["summary"],
            "count": len(res["history"]),
            "history": res["history"]
        }
    except Exception as e:
        logger.error(f"Error fetching neurobets history: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/neurobets/bankroll")
def read_bankroll():
    try:
        return {"status": "success", **get_bankroll_state()}
    except Exception as e:
        logger.error(f"Error fetching bankroll state: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/neurobets/live-bets")
def read_live_bets(
    limit: int = Query(100, description="Items limit"),
    offset: int = Query(0, description="Items offset"),
):
    try:
        res = get_live_bets(limit=limit, offset=offset)
        return {"status": "success", "total": res["total"], "items": res["items"]}
    except Exception as e:
        logger.error(f"Error fetching live bets: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/admin/bankroll/reset")
def admin_reset_bankroll(req: BankrollResetRequest):
    if req.account not in ("training", "live"):
        raise HTTPException(status_code=400, detail="account must be 'training' or 'live'")
    if req.account == "live":
        # backend is the sole writer of the live account now — no need to hop to ai_service.
        acc = reset_live_account(start_balance=req.start_balance)
        return {"status": "success", "account": acc}
    try:
        with httpx.Client(timeout=10.0) as client:
            res = client.post(f"{AI_SERVICE_URL}/bankroll/reset", json=req.dict(exclude_none=True))
            if res.status_code == 200:
                return res.json()
            raise HTTPException(status_code=res.status_code, detail=res.text)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error communicating with AI Service: {e}")
        raise HTTPException(status_code=502, detail="Failed to reach AI service")

@app.post("/api/trigger-scrape")
def trigger_scrape(background_tasks: BackgroundTasks):
    background_tasks.add_task(run_scrape_task)
    return {"status": "success", "message": "Manual scrape triggered in background"}

@app.post("/api/admin/login")
def admin_login(req: AdminLoginRequest):
    if req.username == "diflector" and req.password == "!Zz128500246315":
        return {"status": "success", "token": "diflector-admin-secret-token", "username": "diflector"}
    raise HTTPException(status_code=401, detail="Неверное имя пользователя или пароль")

@app.get("/api/admin/ai-settings")
def read_admin_ai_settings():
    try:
        with httpx.Client(timeout=5.0) as client:
            res = client.get(f"{AI_SERVICE_URL}/settings")
            if res.status_code == 200:
                return res.json()
    except Exception as e:
        logger.error(f"Error communicating with AI Service: {e}")
    return {"status": "success", "settings": {"ai_enabled": True, "training_enabled": True}}

@app.post("/api/admin/ai-settings")
def update_admin_ai_settings(req: AISettingsRequest):
    try:
        with httpx.Client(timeout=5.0) as client:
            res = client.post(f"{AI_SERVICE_URL}/settings", json=req.dict(exclude_none=True))
            if res.status_code == 200:
                return res.json()
    except Exception as e:
        logger.error(f"Error communicating with AI Service: {e}")
    return {"status": "error", "message": "Failed to update AI settings"}

@app.get("/api/admin/ai-logs")
def read_admin_ai_logs():
    try:
        with httpx.Client(timeout=5.0) as client:
            res = client.get(f"{AI_SERVICE_URL}/logs")
            if res.status_code == 200:
                return res.json()
    except Exception as e:
        logger.error(f"Error communicating with AI Service: {e}")
    return {"status": "success", "logs": []}

@app.get("/api/admin/training-health")
def admin_training_health():
    try:
        with httpx.Client(timeout=5.0) as client:
            res = client.get(f"{AI_SERVICE_URL}/training-health")
            if res.status_code == 200:
                return res.json()
    except Exception as e:
        logger.error(f"Error fetching training health: {e}")
    # "unknown", not "ok" — ai_service being unreachable is not the same thing as "no
    # overfitting signals active", and the admin panel's status block should show that
    # distinction (grey/unknown) rather than falsely reporting green.
    return {"status": "success", "health": {"status": "unknown", "signals": {}}}

@app.post("/api/admin/reset-model")
def admin_reset_model():
    # The UPDATE on finished_bets (clearing trained_count across potentially 100k+ rows)
    # plus the model wipe should both be quick, but this gets a longer budget than the
    # 5-10s used by the simple toggle proxies below just in case the archive is large.
    try:
        with httpx.Client(timeout=60.0) as client:
            res = client.post(f"{AI_SERVICE_URL}/reset-model")
            if res.status_code == 200:
                return res.json()
            raise HTTPException(status_code=res.status_code, detail=res.text)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error resetting neural network via AI Service: {e}")
        raise HTTPException(status_code=502, detail="Failed to reach AI service for model reset")

@app.post("/api/admin/backtest")
def admin_run_backtest(payload: Dict[str, Any] = Body(default={})):
    # A large --limit backtest (default 15000 resolved bets) can take from several
    # seconds up to roughly a minute of CPU-bound torch/LightGBM inference on
    # ai_service's side — well past the 5-10s timeout the other admin proxies use here,
    # so this one gets its own generous budget.
    try:
        with httpx.Client(timeout=300.0) as client:
            res = client.post(f"{AI_SERVICE_URL}/backtest", json=payload)
            if res.status_code == 200:
                return res.json()
            raise HTTPException(status_code=res.status_code, detail=res.text)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error running backtest via AI Service: {e}")
        raise HTTPException(status_code=502, detail="Failed to reach AI service for backtest")

@app.get("/api/admin/backtest/history")
def admin_backtest_history():
    try:
        with httpx.Client(timeout=15.0) as client:
            res = client.get(f"{AI_SERVICE_URL}/backtest/history")
            if res.status_code == 200:
                return res.json()
    except Exception as e:
        logger.error(f"Error fetching backtest history: {e}")
    return {"status": "success", "runs": []}

@app.get("/api/ai/overview")
def read_ai_overview(
    bet_types_limit: int = Query(5, ge=0, le=200, description="Max bet-type rows per sport in bet_type_stats.bet_types (does not affect roi_stats). 0 omits them, keeping only each sport's totals."),
    logs_limit: int = Query(60, ge=0, le=300, description="Max recent AI log entries to include, newest first."),
    backtest_runs: int = Query(10, ge=0, le=50, description="Max recent backtest history entries to include, newest first."),
):
    """
    Single consolidated read of everything the admin panel's "Статистика"/"Нейроставки"
    pages and the AI diagnostics show, as one public GET — so reviewing model health
    doesn't require exporting several HTML pages by hand. Combines:
      - db_stats: archive size / resolved-bet counts (same as the admin panel's DB block)
      - bet_type_stats: guess-rate by sport and bet type (the "Статистика" page's
        per-sport breakdown; bet_types_limit trims each sport's tail of rarely-seen
        markets, since the full breakdown can run into the hundreds of rows)
      - roi_stats: flat-stake ROI and Brier vs. the bare bookmaker-implied baseline,
        bucketed by coefficient (the "Статистика" page's headline ROI table)
      - bankroll: live + training account balances
      - ai_settings: whether inference/training are currently toggled on
      - training_health: the overfitting traffic light (see get_training_health)
      - backtest_history: recent runs from the admin panel's "Бэктест" button
      - recent_ai_logs: the live TRAINING/INFERENCE/BANKROLL log feed
    No auth — matches every other /api/* read endpoint in this app (there is no
    server-side session check anywhere beyond the login endpoint itself validating a
    password once).
    """
    try:
        db_stats = get_db_stats()
        bt_stats = get_bet_type_stats()
        for sport in bt_stats.get("sports", []):
            sport["bet_types"] = sport["bet_types"][:bet_types_limit]
        roi = get_roi_stats()
        bankroll = get_bankroll_state()
    except Exception as e:
        logger.error(f"Error building AI overview (local data): {e}")
        raise HTTPException(status_code=500, detail=str(e))

    ai_settings = read_admin_ai_settings().get("settings")
    training_health = admin_training_health().get("health")
    backtest_hist = (admin_backtest_history().get("runs") or [])[:backtest_runs]
    ai_logs = (read_admin_ai_logs().get("logs") or [])[:logs_limit]

    return {
        "status": "success",
        "generated_at": now_moscow().strftime("%Y-%m-%d %H:%M:%S"),
        "db_stats": db_stats,
        "bet_type_stats": bt_stats,
        "roi_stats": roi,
        "bankroll": bankroll,
        "ai_settings": ai_settings,
        "training_health": training_health,
        "backtest_history": backtest_hist,
        "recent_ai_logs": ai_logs,
    }

@app.post("/api/admin/live-bets/cancel-all")
def admin_cancel_live_bets():
    try:
        result = cancel_open_live_bets()
        push_ai_logs(result.get("messages", []))
        return {
            "status": "success",
            "cancelled": result["cancelled"],
            "refunded": result["refunded"],
            "message": f"Отменено ставок: {result['cancelled']}, возвращено {result['refunded']:.1f} ₽." if result["cancelled"]
                       else "Открытых ставок не найдено.",
        }
    except Exception as e:
        logger.error(f"Error cancelling live bets: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/admin/reset-db/live")
def admin_reset_live_db():
    try:
        reset_live_database()
        return {"status": "success", "message": "LIVE база данных успешно обнулена."}
    except Exception as e:
        logger.error(f"Error resetting LIVE DB: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/admin/reset-db/all")
def admin_reset_all_db():
    try:
        reset_all_databases()
        return {"status": "success", "message": "ВСЕ базы данных и чекпоинты модели успешно обнулены."}
    except Exception as e:
        logger.error(f"Error resetting ALL DBs: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ---------------------------------------------------------------------------
# Internal API — used only by ai_service over the docker network. backend is the
# sole writer of live_bets/bankroll_accounts; the neural net reads its balance and
# proposes bet candidates here instead of touching those tables directly (see
# database.place_live_bet_candidates for why: it lets backend re-validate market
# freshness against its own live data at the moment of placement).
# ---------------------------------------------------------------------------

@app.get("/api/internal/live-bankroll")
def internal_read_live_bankroll():
    return {"status": "success", "account": get_live_account()}

@app.post("/api/internal/live-bets")
def internal_place_live_bets(payload: Dict[str, Any] = Body(...)):
    candidates = payload.get("candidates") or []
    result = place_live_bet_candidates(candidates)
    push_ai_logs(result.get("messages", []))
    return {"status": "success", "placed": result["placed"], "skipped": result["skipped"]}

