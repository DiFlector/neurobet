"""Parser and Pydantic validator for LLM output."""

import json
import logging
import re
from typing import Any, Dict, Tuple
from pydantic import ValidationError
from contracts import LLMStructuredVerdict

logger = logging.getLogger("llm.parser")


class LLMOutputParser:
    """Robust parser for local LLM output enforcing Section 20 contract."""

    @staticmethod
    def clean_json_text(text: str) -> str:
        """Strips markdown code blocks, preamble, and extracts first JSON object."""
        cleaned = text.strip()

        # Remove markdown code fences if present (```json ... ``` or ``` ...)
        fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, re.DOTALL)
        if fence_match:
            return fence_match.group(1).strip()

        # Find first '{' and matching last '}'
        start_idx = cleaned.find("{")
        end_idx = cleaned.rfind("}")
        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
            return cleaned[start_idx : end_idx + 1].strip()

        return cleaned

    @classmethod
    def parse_and_validate(
        cls,
        raw_output: str,
        market_type: str = "unknown",
        selection_id: str = "unknown",
        model_identifier: str = "unknown",
        prompt_version: str = "neurobet_llm_v1.0",
        latency_ms: float = 0.0,
    ) -> LLMStructuredVerdict:
        """
        Parses raw text into validated LLMStructuredVerdict.
        Guaranteed to never raise: on any invalidity returns INSUFFICIENT_DATA fallback.
        """
        cleaned_json = cls.clean_json_text(raw_output)

        try:
            parsed_dict = json.loads(cleaned_json)
            if not isinstance(parsed_dict, dict):
                raise ValueError("Parsed JSON root is not an object/dictionary")

            # Fill telemetry defaults if model omitted them
            if "market_type" not in parsed_dict or not parsed_dict["market_type"]:
                parsed_dict["market_type"] = market_type
            if "selection_id" not in parsed_dict or not parsed_dict["selection_id"]:
                parsed_dict["selection_id"] = selection_id
            parsed_dict["latency_ms"] = latency_ms
            parsed_dict["model_identifier"] = model_identifier
            parsed_dict["prompt_version"] = prompt_version

            # Validate against Pydantic schema
            verdict = LLMStructuredVerdict.model_validate(parsed_dict)
            return verdict

        except (json.JSONDecodeError, ValueError) as err:
            logger.warning(f"Corrupt JSON output from LLM: {err}. Raw: {raw_output[:200]}")
            return LLMStructuredVerdict.create_insufficient_data_fallback(
                market_type=market_type,
                selection_id=selection_id,
                reason=f"CORRUPT_JSON_OUTPUT: {str(err)}",
                model_identifier=model_identifier,
                prompt_version=prompt_version,
                latency_ms=latency_ms,
            )
        except ValidationError as err:
            logger.warning(f"Pydantic validation failure on LLM output: {err}")
            return LLMStructuredVerdict.create_insufficient_data_fallback(
                market_type=market_type,
                selection_id=selection_id,
                reason="PYDANTIC_SCHEMA_VALIDATION_ERROR",
                model_identifier=model_identifier,
                prompt_version=prompt_version,
                latency_ms=latency_ms,
            )
