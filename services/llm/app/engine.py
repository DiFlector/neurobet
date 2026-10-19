"""LLM Inference Engines: GGUF via llama.cpp and Deterministic Engine."""

import abc
import json
import logging
import os
import re
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple
from contracts import LLMAnalysisInput, LLMStructuredVerdict
from .config import config
from .hardware import HardwareManager
from .parser import LLMOutputParser
from .prompt import PROMPT_VERSION, SYSTEM_INSTRUCTION, format_user_prompt

logger = logging.getLogger("llm.engine")

_CYR_MAP = str.maketrans(
    {
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
        "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
        "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
        "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
        "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    }
)


def _participant_tokens(name: str) -> Set[str]:
    tokens: Set[str] = set()
    for part in re.split(r"[/|,]+", name or ""):
        for w in re.findall(r"[A-Za-zА-Яа-яЁё]{3,}", part):
            low = w.lower()
            tokens.add(low)
            lat = low.translate(_CYR_MAP)
            if len(lat) >= 3:
                tokens.add(lat)
    return tokens


def _signal_about_participants(
    title: str,
    snippet: str,
    participant_names: Sequence[str],
    keywords: Iterable[str],
    window_words: int = 4,
) -> bool:
    """Injury/fatigue only if attributed to home/away, not a third player."""
    low = f"{title} {snippet}".lower()
    tokens: Set[str] = {"игрок", "player", "теннисист", "спортсмен"}
    for name in participant_names:
        tokens |= _participant_tokens(name)
    if not tokens:
        return False

    subjects: List[str] = []
    for pat in (
        r"injured\s+([a-zа-яё]+(?:[\s\-]+[a-zа-яё]+)?)",
        r"([a-zа-яё]+(?:[\s\-]+[a-zа-яё]+)?)\s+(?:retired|withdrawn|withdrew)",
        r"травм\w*\s+(?:у\s+)?([a-zа-яё]+)",
        r"([a-zа-яё]+)\s+травм",
        r"дисквалифицир\w*\s+([a-zа-яё]+)",
        r"([a-zа-яё]+)\s+дисквалифицир",
    ):
        for m in re.finditer(pat, low, flags=re.IGNORECASE):
            subjects.append(m.group(1))
    if subjects:
        for sub in subjects:
            sub_l = sub.lower()
            sub_lat = sub_l.translate(_CYR_MAP)
            if any(tok in sub_l or tok in sub_lat for tok in tokens):
                return True
        return False

    words = re.findall(r"[a-zа-яё0-9]+", low)
    kw_set = {k.lower() for k in keywords}
    for i, w in enumerate(words):
        if not any(kw in w or w.startswith(kw) for kw in kw_set):
            continue
        lo, hi = max(0, i - window_words), min(len(words), i + window_words + 1)
        window = " ".join(words[lo:hi])
        window_lat = window.translate(_CYR_MAP)
        if any(tok in window or tok in window_lat for tok in tokens):
            return True
    return False


class BaseInferenceEngine(abc.ABC):
    """Abstract interface for LLM inference."""

    @abc.abstractmethod
    def analyze(self, input_data: LLMAnalysisInput) -> LLMStructuredVerdict:
        """Runs qualitative analysis on structured input and returns structured verdict."""
        pass

    @abc.abstractmethod
    def complete(self, messages: List[Dict[str, str]], temperature: float = 0.1, max_tokens: int = 512) -> str:
        """Runs raw chat completion for OpenAI compatibility."""
        pass

    @property
    @abc.abstractmethod
    def model_identifier(self) -> str:
        pass

    @property
    @abc.abstractmethod
    def device(self) -> str:
        pass


class DeterministicEngine(BaseInferenceEngine):
    """
    High-performance deterministic reasoning engine.
    Used when GGUF weights are not yet downloaded or in lightweight environments.
    Strictly follows Section 19/20 contracts and generates 100% reproducible JSON.
    """

    def __init__(self, model_identifier: str = "deterministic-instruct-v1"):
        self._model_identifier = model_identifier
        self._device = "cpu"
        logger.info(f"Initialized DeterministicEngine with identifier '{self._model_identifier}'")

    @property
    def model_identifier(self) -> str:
        return self._model_identifier

    @property
    def device(self) -> str:
        return self._device

    def analyze(self, input_data: LLMAnalysisInput) -> LLMStructuredVerdict:
        start_time = time.perf_counter()

        # Deterministic evaluation of qualitative signals:
        edge = input_data.ml.edge
        ml_prob = input_data.ml.probability
        confidence = input_data.ml.confidence

        injury_keywords = ["травм", "боль", "снялся", "снялась", "поврежд", "injury", "pain", "retire"]
        fatigue_keywords = ["устал", "тяжелый матч", "3 сета подряд", "перелет", "fatigue", "exhaust"]

        has_injury_risk = False
        has_fatigue_risk = False
        evidence_list = []
        participants = [input_data.event.home, input_data.event.away]

        for r in input_data.research:
            title = r.title or ""
            snippet = r.snippet or ""
            # Только сигнал про участников этого матча — не чужая травма из той же статьи.
            if _signal_about_participants(title, snippet, participants, injury_keywords):
                has_injury_risk = True
                evidence_list.append(
                    {"url": None, "domain": r.domain, "note": f"Injury signal: {snippet[:80]}"}
                )
            if _signal_about_participants(title, snippet, participants, fatigue_keywords):
                has_fatigue_risk = True
                evidence_list.append(
                    {"url": None, "domain": r.domain, "note": f"Fatigue signal: {snippet[:80]}"}
                )

        injury_risk_score = 0.8 if has_injury_risk else 0.0
        fatigue_risk_score = 0.8 if has_fatigue_risk else 0.0
        news_risk_score = 0.7 if (has_injury_risk or has_fatigue_risk) else 0.0
        context_side_a = 0.5
        pending = input_data.ml.model_version == "pending"

        reason_codes = []
        contradictions = []

        matchup = f"{input_data.event.home} — {input_data.event.away}"
        selection = input_data.market.selection
        selection_ru = {
            "player_a": "участник 1",
            "player_b": "участник 2",
            "team_a": "команда 1",
            "team_b": "команда 2",
        }.get(str(selection).lower(), selection)

        if pending and not has_injury_risk and not has_fatigue_risk:
            if input_data.research:
                verdict = "BET"
                reason_codes.append("NO_NEGATIVE_SIGNALS")
                stake_fraction = 0.0
                qual_conf = 0.4
                summary = f"{matchup}: внешний контекст без явного перевеса стороны."
            else:
                verdict = "INSUFFICIENT_DATA"
                reason_codes.append("INSUFFICIENT_DATA")
                stake_fraction = 0.0
                qual_conf = 0.0
                summary = f"{matchup}: в источниках нет информации о матче."
        elif has_injury_risk:
            verdict = "NO_BET"
            reason_codes.append("INJURY_RISK")
            contradictions.append(
                "Внешние источники указывают на травму (injury) вопреки статистическому перевесу модели"
            )
            stake_fraction = 0.0
            qual_conf = max(0.2, confidence - 0.3)
            summary = (
                f"{matchup}: против ставки на «{selection_ru}» — "
                f"во внешних источниках есть сообщение о травме."
            )
        elif has_fatigue_risk:
            verdict = "NO_BET"
            reason_codes.append("SCHEDULE_FATIGUE")
            contradictions.append("Выявлены признаки усталости и плотного графика")
            stake_fraction = 0.0
            qual_conf = max(0.3, confidence - 0.2)
            summary = (
                f"{matchup}: против ставки на «{selection_ru}» — "
                f"выявлена сильная физическая усталость."
            )
        elif (edge >= 0.04 and ml_prob >= 0.58) if not input_data.research else (edge > 0.02 and ml_prob >= 0.52):
            verdict = "BET"
            reason_codes.append("MODEL_EDGE")
            reason_codes.append("NO_NEGATIVE_SIGNALS")
            stake_fraction = min(0.015, input_data.constraints.max_stake_fraction)
            qual_conf = min(0.95, confidence + (0.05 if input_data.research else 0.0))
            summary = (
                f"{matchup}: качественный анализ поддерживает выбор «{selection_ru}» "
                f"со статистическим перевесом {edge:.2%} и без негативных сигналов."
            )
        else:
            verdict = "NO_BET"
            reason_codes.append("MARGINAL_EDGE_NO_RESEARCH" if not input_data.research else "MARGINAL_EDGE")
            stake_fraction = 0.0
            qual_conf = 0.50
            summary = (
                f"{matchup}: без ставки — перевес {edge:.2%} недостаточный "
                f"или низкая статистическая уверенность для выбора «{selection_ru}»."
            )

        elapsed_ms = (time.perf_counter() - start_time) * 1000.0

        return LLMStructuredVerdict(
            verdict=verdict,
            market_type=input_data.market.type,
            selection_id=input_data.market.selection,
            confidence=round(qual_conf, 3),
            stake_recommendation_fraction=round(stake_fraction, 4),
            reason_codes=reason_codes,
            contradictions=contradictions,
            research_quality=0.85 if input_data.research else 0.5,
            research_freshness_seconds=120 if input_data.research else None,
            invalid_or_missing_data=[],
            summary=summary,
            evidence=evidence_list,
            latency_ms=round(elapsed_ms, 2),
            model_identifier=self._model_identifier,
            prompt_version=PROMPT_VERSION,
            context_side_a=context_side_a,
            injury_risk_score=injury_risk_score,
            fatigue_risk_score=fatigue_risk_score,
            news_risk_score=news_risk_score,
        )

    def complete(self, messages: List[Dict[str, str]], temperature: float = 0.1, max_tokens: int = 512) -> str:
        # Simple OpenAI-compatible completion
        user_msg = next((m.get("content", "") for m in messages if m.get("role") == "user"), "")
        return json.dumps({
            "verdict": "BET",
            "market_type": "match_winner",
            "selection_id": "player_a",
            "confidence": 0.85,
            "stake_recommendation_fraction": 0.01,
            "reason_codes": ["MODEL_EDGE"],
            "contradictions": [],
            "research_quality": 0.8,
            "research_freshness_seconds": 300,
            "invalid_or_missing_data": [],
            "summary": "Deterministic completion",
            "evidence": [],
        })


class GGUFEngine(BaseInferenceEngine):
    """
    GGUF model inference engine running on CPU or NVIDIA GTX 1050 Ti.
    Includes automatic GPU offload with CPU fallback.
    """

    def __init__(
        self,
        model_path: str,
        context_size: int = 4096,
        requested_gpu_layers: str = "auto",
        fallback_engine: Optional[BaseInferenceEngine] = None,
    ):
        self.model_path = model_path
        self.context_size = context_size
        self.fallback_engine = fallback_engine or DeterministicEngine(model_identifier="fallback-deterministic")
        self.llama = None
        self._device = "cpu"
        self._model_identifier = os.path.basename(model_path) if model_path else "unknown_gguf"

        # Check if model exists
        if not os.path.isfile(model_path):
            logger.warning(
                f"GGUF model not found at '{model_path}'. Using fallback engine '{self.fallback_engine.model_identifier}'."
            )
            return

        # Attempt to load llama-cpp-python
        try:
            from llama_cpp import Llama  # type: ignore

            gpu_layers, device = HardwareManager.calculate_gpu_layers(requested_gpu_layers)
            logger.info(f"Loading GGUF '{model_path}' with {gpu_layers} GPU layers on {device}...")

            try:
                self.llama = Llama(
                    model_path=model_path,
                    n_ctx=context_size,
                    n_gpu_layers=gpu_layers,
                    verbose=False,
                )
                self._device = device
                logger.info(f"GGUF successfully loaded on {self._device} ({gpu_layers} layers).")
            except Exception as e:
                logger.warning(f"Failed loading with GPU layers: {e}. Retrying with CPU fallback (n_gpu_layers=0)...")
                self.llama = Llama(
                    model_path=model_path,
                    n_ctx=context_size,
                    n_gpu_layers=0,
                    verbose=False,
                )
                self._device = "cpu"
                logger.info("GGUF successfully loaded on CPU fallback.")

        except ImportError:
            logger.warning("llama-cpp-python not installed in this environment. Using fallback engine.")
            self.llama = None
        except Exception as e:
            logger.error(f"Error loading GGUF model: {e}. Using fallback engine.")
            self.llama = None

    @property
    def model_identifier(self) -> str:
        if self.llama:
            return f"{self._model_identifier}-{self._device}"
        return self.fallback_engine.model_identifier

    @property
    def device(self) -> str:
        if self.llama:
            return self._device
        return self.fallback_engine.device

    def analyze(self, input_data: LLMAnalysisInput) -> LLMStructuredVerdict:
        if not self.llama:
            return self.fallback_engine.analyze(input_data)

        start_time = time.perf_counter()
        prompt_text = format_user_prompt(input_data)
        messages = [
            {"role": "system", "content": SYSTEM_INSTRUCTION},
            {"role": "user", "content": prompt_text},
        ]

        try:
            response = self.llama.create_chat_completion(
                messages=messages,
                temperature=config.temperature,
                top_p=config.top_p,
                max_tokens=600,
            )
            raw_content = response["choices"][0]["message"]["content"]
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0

            return LLMOutputParser.parse_and_validate(
                raw_output=raw_content,
                market_type=input_data.market.type,
                selection_id=input_data.market.selection,
                model_identifier=self.model_identifier,
                prompt_version=PROMPT_VERSION,
                latency_ms=elapsed_ms,
            )
        except Exception as e:
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0
            logger.error(f"Error during GGUF generation: {e}")
            return LLMStructuredVerdict.create_insufficient_data_fallback(
                market_type=input_data.market.type,
                selection_id=input_data.market.selection,
                reason=f"LLM_INFERENCE_ERROR: {str(e)}",
                model_identifier=self.model_identifier,
                prompt_version=PROMPT_VERSION,
                latency_ms=elapsed_ms,
            )

    def complete(self, messages: List[Dict[str, str]], temperature: float = 0.1, max_tokens: int = 512) -> str:
        if not self.llama:
            return self.fallback_engine.complete(messages, temperature, max_tokens)
        response = self.llama.create_chat_completion(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return response["choices"][0]["message"]["content"]
