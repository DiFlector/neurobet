"""Configuration settings for Neurobet Local LLM Service."""

import os
from typing import Union


class LLMConfig:
    """LLM runtime configuration."""

    def __init__(self):
        self.model_path: str = os.getenv(
            "LLM_MODEL_PATH", "/models/checkpoints/qwen2.5-3b-instruct-q4_k_m.gguf"
        )
        self.context_size: int = int(os.getenv("LLM_CONTEXT_SIZE", "4096"))
        self.gpu_layers: str = os.getenv("LLM_GPU_LAYERS", "auto")
        self.temperature: float = float(os.getenv("LLM_TEMPERATURE", "0.1"))
        self.top_p: float = float(os.getenv("LLM_TOP_P", "0.9"))
        self.prompt_version: str = os.getenv("LLM_PROMPT_VERSION", "neurobet_llm_v1.0")
        self.mock_fallback: bool = os.getenv("LLM_MOCK_FALLBACK", "true").lower() in ("true", "1", "yes")
        self.log_level: str = os.getenv("LOG_LEVEL", "INFO")


config = LLMConfig()
