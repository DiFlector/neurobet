import os
from datetime import datetime, timezone
from typing import Any, Dict, List
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from sports_core import registry
try:
    import tennis_adapter
except ImportError:
    pass

app = FastAPI(
    title="Neurobet API",
    version="1.0.0",
    description="Research & Betting Simulation API for Neurobet (Primary Sport: Tennis)",
    root_path=os.getenv("BACKEND_ROOT_PATH", ""),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class HealthResponse(BaseModel):
    status: str
    app: str
    primary_sport: str
    server_time_utc: str
    timezone: str
    version: str


@app.get("/health", response_model=HealthResponse)
def health_check() -> Dict[str, Any]:
    return {
        "status": "healthy",
        "app": "neurobet-backend",
        "primary_sport": os.getenv("PRIMARY_SPORT", "tennis"),
        "server_time_utc": datetime.now(timezone.utc).isoformat(),
        "timezone": "UTC",
        "version": "1.0.0",
    }


@app.get("/api/sports")
def get_supported_sports() -> List[Dict[str, Any]]:
    """Returns all active sports registered in the system."""
    primary = os.getenv("PRIMARY_SPORT", "tennis").lower()
    sports = []
    # If no sports dynamically registered yet, ensure tennis is present
    active_sports = registry.list_sports() or ["tennis"]
    for sport_code in active_sports:
        desc = registry.get_descriptor(sport_code)
        d = desc.model_dump()
        d["is_primary"] = (sport_code == primary)
        sports.append(d)
    return sports


@app.get("/api/sports/{sport_code}")
def get_sport_details(sport_code: str) -> Dict[str, Any]:
    """
    Returns sport descriptor. If sport is not implemented, returns
    status="UNSUPPORTED" and supported=False cleanly without error.
    """
    desc = registry.get_descriptor(sport_code)
    res = desc.model_dump()
    primary = os.getenv("PRIMARY_SPORT", "tennis").lower()
    res["is_primary"] = (sport_code.lower() == primary)
    return res


@app.get("/api/tennis/events")
def get_tennis_events() -> List[Dict[str, Any]]:
    """Placeholder endpoint for tennis events list."""
    return []


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=False)
