import os
from datetime import datetime, timezone
from typing import Dict, Any, List
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

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
    return [
        {
            "code": "tennis",
            "name": "Теннис",
            "status": "active",
            "is_primary": True,
            "markets": ["match_winner", "set_winner", "total_games"],
        }
    ]


@app.get("/api/tennis/events")
def get_tennis_events() -> List[Dict[str, Any]]:
    """Placeholder endpoint for tennis events list."""
    return []


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=False)
