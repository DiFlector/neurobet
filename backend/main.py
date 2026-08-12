import logging
import datetime
import os
import httpx
from fastapi import FastAPI, Query, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from typing import Optional, List, Dict, Any
from pydantic import BaseModel

from database import init_db, save_parsed_events, get_live_matches, get_odds_history, get_db_stats, get_top_neurobets, get_neurobets_history, reset_live_database, reset_all_databases
from parser_service import FonbetParserService
from settings import settings

class AdminLoginRequest(BaseModel):
    username: str
    password: str

class AISettingsRequest(BaseModel):
    ai_enabled: Optional[bool] = None
    training_enabled: Optional[bool] = None

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

def trigger_ai_pipeline():
    try:
        with httpx.Client(timeout=30.0) as client:
            res = client.post(f"{AI_SERVICE_URL}/predict-and-train")
            if res.status_code == 200:
                logger.info(f"AI Service trigger response: {res.json()}")
    except Exception as e:
        logger.error(f"Failed to communicate with AI Service container at {AI_SERVICE_URL}: {e}")

def run_scrape_task():
    logger.info("Executing background Fonbet LIVE scrape task...")
    try:
        parsed_events = parser_service.parse_live()
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        save_parsed_events(parsed_events, now_str)
        logger.info(f"Successfully scraped and stored {len(parsed_events)} events at {now_str}")
        
        # Trigger AI Service Microservice Container (Port 8001)
        trigger_ai_pipeline()
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
        return {
            "status": "success",
            "count": len(matches),
            "matches": matches
        }
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
    min_odds: float = Query(1.1, description="Min odds bound"),
    max_odds: float = Query(2.1, description="Max odds bound")
):
    try:
        top_bets = get_top_neurobets(
            sport_filter=sport,
            sort_mode=sort,
            min_odds=min_odds,
            max_odds=max_odds
        )
        return {
            "status": "success",
            "count": len(top_bets),
            "bets": top_bets
        }
    except Exception as e:
        logger.error(f"Error fetching neurobets: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/neurobets/history")
def read_neurobets_history(
    sport: Optional[str] = Query(None, description="Filter by sport path"),
    search: Optional[str] = Query(None, description="Search by team or match name"),
    min_odds: float = Query(1.1, description="Min odds bound"),
    max_odds: float = Query(2.1, description="Max odds bound"),
    limit: int = Query(50, description="Items limit"),
    offset: int = Query(0, description="Items offset")
):
    try:
        res = get_neurobets_history(
            sport_filter=sport,
            search=search,
            min_odds=min_odds,
            max_odds=max_odds,
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

