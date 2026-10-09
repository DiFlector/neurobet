"""FastAPI HTTP service for Neurobet Local LLM Inference."""

from datetime import datetime, timezone
import logging
import os
import time
from typing import Any, Dict, List, Optional
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from contracts import LLMAnalysisInput, LLMStructuredVerdict
from .config import config
from .engine import BaseInferenceEngine, DeterministicEngine, GGUFEngine
from .hardware import HardwareManager
from .parser import LLMOutputParser
from .prompt import PROMPT_VERSION

logging.basicConfig(
    level=config.log_level,
    format="%(asctime)s UTC [%(levelname)s] [llm] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("llm")

app = FastAPI(
    title="Neurobet Local LLM Service",
    description="Qualitative analytical inference engine with strict JSON schema enforcement",
    version="1.0.0",
)

try:
    from observability import setup_observability
    setup_observability(app, service_name="llm")
except ImportError:
    pass

# Initialize engine on startup
def get_engine() -> BaseInferenceEngine:
    if not hasattr(app.state, "engine"):
        has_gpu, gpu_info = HardwareManager.detect_nvidia_gpu()
        logger.info(f"Hardware check: GPU available={has_gpu}, GPU Info={gpu_info}")

        app.state.engine = GGUFEngine(
            model_path=config.model_path,
            context_size=config.context_size,
            requested_gpu_layers=config.gpu_layers,
            fallback_engine=DeterministicEngine(model_identifier="neurobet-deterministic-v1.0"),
        )
    return app.state.engine


@app.on_event("startup")
def startup_event():
    logger.info(f"Starting Neurobet Local LLM service (prompt={PROMPT_VERSION})...")
    get_engine()


@app.get("/health")
def health() -> Dict[str, Any]:
    """Health check reporting model, device, and hardware details."""
    engine = get_engine()
    has_gpu, gpu_info = HardwareManager.detect_nvidia_gpu()

    return {
        "status": "healthy",
        "service": "llm",
        "model_identifier": engine.model_identifier,
        "device": engine.device,
        "prompt_version": PROMPT_VERSION,
        "context_size": config.context_size,
        "gpu_available": has_gpu,
        "gpu_details": gpu_info,
        "utc_time": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/api/analyze", response_model=LLMStructuredVerdict)
def analyze(input_data: LLMAnalysisInput) -> LLMStructuredVerdict:
    """
    Main analytical endpoint accepting Section 19 input contract
    and returning validated Section 20 LLMStructuredVerdict.
    """
    engine = get_engine()
    verdict = engine.analyze(input_data)
    return verdict


class ValidateRequest(BaseModel):
    raw_output: str
    market_type: str = "match_winner"
    selection_id: str = "player_a"


@app.post("/api/validate", response_model=LLMStructuredVerdict)
def validate_output(req: ValidateRequest) -> LLMStructuredVerdict:
    """
    Validates arbitrary raw LLM output string against Section 20 Pydantic schema.
    Returns parsed verdict or INSUFFICIENT_DATA fallback.
    """
    engine = get_engine()
    return LLMOutputParser.parse_and_validate(
        raw_output=req.raw_output,
        market_type=req.market_type,
        selection_id=req.selection_id,
        model_identifier=engine.model_identifier,
        prompt_version=PROMPT_VERSION,
        latency_ms=0.0,
    )


# OpenAI Compatible Chat Completions API (Architecture Section 17)
class ChatMessage(BaseModel):
    role: str
    content: str


class ChatCompletionRequest(BaseModel):
    model: Optional[str] = "local-llm"
    messages: List[ChatMessage]
    temperature: Optional[float] = 0.1
    max_tokens: Optional[int] = 512


@app.post("/v1/chat/completions")
def chat_completions(req: ChatCompletionRequest) -> Dict[str, Any]:
    """OpenAI-compatible chat completion endpoint."""
    engine = get_engine()
    messages_dicts = [{"role": m.role, "content": m.content} for m in req.messages]
    content = engine.complete(
        messages=messages_dicts,
        temperature=req.temperature or 0.1,
        max_tokens=req.max_tokens or 512,
    )
    return {
        "id": f"chatcmpl-{int(time.time())}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": engine.model_identifier,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=False)
