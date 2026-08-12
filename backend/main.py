import logging
import datetime
from fastapi import FastAPI, Query, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from typing import Optional, List, Dict, Any

from database import init_db, save_parsed_events, get_live_matches, get_odds_history, get_db_stats
from parser_service import FonbetParserService
from settings import settings

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

def run_scrape_task():
    logger.info("Executing background Fonbet LIVE scrape task...")
    try:
        parsed_events = parser_service.parse_live()
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        save_parsed_events(parsed_events, now_str)
        logger.info(f"Successfully scraped and stored {len(parsed_events)} events at {now_str}")
    except Exception as e:
        logger.error(f"Error during scheduled scrape: {e}", exc_info=True)

@app.on_event("startup")
def startup_event():
    logger.info("Initializing Database...")
    init_db()
    
    # Run initial scrape immediately
    logger.info("Running initial scrape on startup...")
    run_scrape_task()

    # Schedule recurring task based on settings
    interval = settings.SCRAPE_INTERVAL_SECONDS
    scheduler.add_job(run_scrape_task, 'interval', seconds=interval, id="fonbet_scraper")
    scheduler.start()
    logger.info(f"Scheduler started! Fonbet LIVE matches will be scraped every {interval} seconds.")

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

@app.post("/api/trigger-scrape")
def trigger_scrape(background_tasks: BackgroundTasks):
    background_tasks.add_task(run_scrape_task)
    return {"status": "success", "message": "Manual scrape triggered in background"}
