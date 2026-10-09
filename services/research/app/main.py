"""FastAPI HTTP service for Neurobet Web Research."""

import asyncio
from datetime import datetime, timezone
import logging
import os
from typing import Any, Dict, List
from fastapi import FastAPI, HTTPException

from contracts import ResearchPacket, ResearchRequest
from .config import config
from .service import WebResearchService

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s UTC [%(levelname)s] [research] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("research")

app = FastAPI(
    title="Neurobet Web Research Service",
    description="Contextual web research and extraction pipeline",
    version="1.0.0",
)

research_service = WebResearchService()


def update_healthy_file():
    try:
        with open("/tmp/healthy", "w") as f:
            f.write(datetime.now(timezone.utc).isoformat())
    except Exception:
        pass


@app.on_event("startup")
async def startup_event():
    logger.info("Web Research Service started.")
    update_healthy_file()

    # Background task to keep /tmp/healthy touched
    async def heartbeat():
        while True:
            update_healthy_file()
            await asyncio.sleep(5)

    asyncio.create_task(heartbeat())


@app.get("/health")
def health() -> Dict[str, Any]:
    """Health check endpoint."""
    update_healthy_file()
    return {
        "status": "healthy",
        "service": "research",
        "enabled": config.enabled,
        "max_pages": config.max_pages,
        "allowlist_count": len(config.allowlist),
        "cache_ttl_seconds": config.cache_ttl_seconds,
        "utc_time": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/api/research", response_model=ResearchPacket)
def execute_research(request: ResearchRequest) -> ResearchPacket:
    """
    Executes web research for event or returns cached packet.
    """
    if not config.enabled:
        raise HTTPException(status_code=503, detail="Web Research is currently disabled in configuration.")

    packet = research_service.execute_research(request)
    return packet


@app.get("/api/research/cache")
def get_cache_info() -> Dict[str, Any]:
    """Returns research cache statistics."""
    return {
        "cache_size": len(research_service.cache._cache),
        "ttl_seconds": research_service.cache.ttl_seconds,
    }


@app.get("/api/research/history")
def get_research_history() -> List[Dict[str, Any]]:
    """Returns recent research executions."""
    return research_service.cache.get_history()


if __name__ == "__main__":
    import uvicorn
    update_healthy_file()
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=False)
