"""
Optional DeepSeek LLM layer for NeuroBet.

All entry points fail soft: missing token, disabled flags, timeouts, or parse
errors return None / empty and never raise into the training or betting loop.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.config import (
    DEEPSEEK_TOKEN,
    LLM_DIGEST_HOURS,
    LLM_DIGEST_MAX_HISTORY,
    LLM_ENABLED,
    LLM_MATCH_CONTEXT,
    LLM_MAX_CONTEXT_PER_CYCLE,
    LLM_MAX_RATIONALE_PER_CYCLE,
    LLM_MIN_INTERVAL_SECONDS,
    LLM_VETO,
    LLM_VETO_MIN_CONFIDENCE,
    MODEL_DIR,
)

logger = logging.getLogger("deepseek_insights")

MOSCOW_TZ = timezone(timedelta(hours=3))

_client_lock = threading.Lock()
_client = None
_last_call_at = 0.0
_cache: Dict[str, tuple[float, Any]] = {}
_CACHE_LOCK = threading.Lock()

DIGEST_PATH = os.path.join(MODEL_DIR, "llm_digest.json")


def llm_is_enabled() -> bool:
    return bool(LLM_ENABLED) and bool(DEEPSEEK_TOKEN and DEEPSEEK_TOKEN.strip())


def match_context_enabled() -> bool:
    return llm_is_enabled() and bool(LLM_MATCH_CONTEXT)


def veto_enabled() -> bool:
    return match_context_enabled() and bool(LLM_VETO)


def digest_hours() -> float:
    return float(LLM_DIGEST_HOURS)


def _get_client():
    global _client
    if _client is not None:
        return _client
    from app.deepseek.client import DeepSeekWebClient

    _client = DeepSeekWebClient()
    return _client


def _rate_limit_wait() -> None:
    global _last_call_at
    gap = float(LLM_MIN_INTERVAL_SECONDS)
    if gap <= 0:
        return
    now = time.monotonic()
    wait = (_last_call_at + gap) - now
    if wait > 0:
        time.sleep(wait)
    _last_call_at = time.monotonic()


def _cache_get(key: str) -> Any:
    with _CACHE_LOCK:
        item = _cache.get(key)
        if not item:
            return None
        expires_at, value = item
        if time.monotonic() > expires_at:
            _cache.pop(key, None)
            return None
        return value


def _cache_set(key: str, value: Any, ttl_seconds: float) -> None:
    with _CACHE_LOCK:
        _cache[key] = (time.monotonic() + max(1.0, ttl_seconds), value)


def _strip_json_fence(text: str) -> str:
    raw = (text or "").strip()
    if not raw:
        return ""
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", raw, re.IGNORECASE)
    if fence:
        return fence.group(1).strip()
    # First {...} block if the model added prose around it.
    brace = re.search(r"\{[\s\S]*\}", raw)
    if brace:
        return brace.group(0)
    return raw


def _parse_json(text: str) -> Optional[Dict[str, Any]]:
    cleaned = _strip_json_fence(text)
    if not cleaned:
        return None
    try:
        data = json.loads(cleaned)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _call_send(
    prompt: str,
    *,
    search: bool,
    thinking: bool,
    timeout: float,
) -> Optional[str]:
    if not llm_is_enabled():
        return None

    def _run() -> str:
        with _client_lock:
            _rate_limit_wait()
            client = _get_client()
            return client.send_message(
                prompt,
                thinking_enabled=thinking,
                search_enabled=search,
            )

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            fut = pool.submit(_run)
            text = fut.result(timeout=max(5.0, timeout))
        return (text or "").strip() or None
    except FuturesTimeout:
        logger.warning("DeepSeek call timed out after %.1fs", timeout)
        try:
            from app.neuralbet.pipeline import add_ai_log

            add_ai_log(
                "SYSTEM",
                f"DeepSeek timeout after {timeout:.0f}s — LLM layer skipped this call.",
                level="WARNING",
            )
        except Exception:
            pass
        return None
    except Exception as e:
        logger.warning("DeepSeek call failed: %s", e)
        try:
            from app.neuralbet.pipeline import add_ai_log

            add_ai_log("SYSTEM", f"DeepSeek error: {e}", level="WARNING")
        except Exception:
            pass
        return None


def ask_text(
    prompt: str,
    *,
    search: bool = False,
    thinking: bool = False,
    timeout: float = 45.0,
) -> Optional[str]:
    return _call_send(prompt, search=search, thinking=thinking, timeout=timeout)


def ask_json(
    prompt: str,
    *,
    search: bool = False,
    thinking: bool = False,
    timeout: float = 45.0,
    retry: bool = True,
) -> Optional[Dict[str, Any]]:
    text = ask_text(prompt, search=search, thinking=thinking, timeout=timeout)
    if not text:
        return None
    parsed = _parse_json(text)
    if parsed is not None:
        return parsed
    if not retry:
        return None
    retry_prompt = (
        prompt
        + "\n\nОтвет должен быть ТОЛЬКО валидным JSON-объектом без markdown и без пояснений."
    )
    text2 = ask_text(retry_prompt, search=search, thinking=thinking, timeout=timeout)
    return _parse_json(text2 or "") if text2 else None


# ---------------------------------------------------------------------------
# Backtest narrative
# ---------------------------------------------------------------------------

def build_backtest_narrative(agent_review: Dict[str, Any]) -> Optional[str]:
    if not llm_is_enabled() or not agent_review:
        return None
    compact = {
        "summary": agent_review.get("summary"),
        "flags": agent_review.get("flags"),
        "funnel": agent_review.get("funnel"),
        "walk_forward_stability": agent_review.get("walk_forward_stability"),
        "slices": {
            "walk_forward": (agent_review.get("slices") or {}).get("walk_forward"),
            "overall": (agent_review.get("slices") or {}).get("overall"),
        },
        "delta_vs_previous": agent_review.get("delta_vs_previous"),
        "head_alignment": agent_review.get("head_alignment"),
    }
    prompt = (
        "Ты аналитик betting ML-системы NeuroBet. По JSON agent_review ниже напиши "
        "краткое резюме на русском (5–10 предложений): есть ли edge (ориентир walk-forward), "
        "проходит ли quality gate, что улучшилось/ухудшилось vs прошлый прогон, главные риски "
        "и 1–2 конкретные рекомендации. Без Markdown-заголовков, без кода, только текст.\n\n"
        f"{json.dumps(compact, ensure_ascii=False)}"
    )
    return ask_text(prompt, search=False, timeout=60.0)


# ---------------------------------------------------------------------------
# Match web-search context + veto
# ---------------------------------------------------------------------------

def _market_side_hint(label: str, market_prefix: str) -> str:
    text = f"{market_prefix or ''} {label or ''}".lower()
    if "тотал бол" in text or "total over" in text or "больше" in text:
        return "over"
    if "тотал мен" in text or "total under" in text or "меньше" in text:
        return "under"
    if "фора" in text or "handicap" in text:
        return "handicap"
    if "п1" in text or "1" == (label or "").strip() or "хозя" in text:
        return "home"
    if "п2" in text or "2" == (label or "").strip() or "гост" in text:
        return "away"
    if "x" in text or "ничья" in text or "draw" in text:
        return "draw"
    return "unknown"


def fetch_match_context(candidate: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Web-search context for one live stake candidate. Cached by event_id."""
    if not match_context_enabled():
        return None

    event_id = candidate.get("event_id")
    cache_key = f"match_ctx:{event_id}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    team_1 = candidate.get("team_1") or ""
    team_2 = candidate.get("team_2") or ""
    match_name = candidate.get("match_name") or f"{team_1} vs {team_2}"
    sport = candidate.get("sport") or ""
    score = candidate.get("score") or ""
    label = candidate.get("label") or ""
    market_prefix = candidate.get("market_prefix") or ""
    coeff = candidate.get("coeff")
    win_prob = candidate.get("win_probability")
    side = _market_side_hint(label, market_prefix)

    prompt = (
        "Ты спортивный аналитик. Используй веб-поиск. Матч LIVE.\n"
        f"Спорт: {sport}\n"
        f"Матч: {match_name} ({team_1} vs {team_2})\n"
        f"Текущий счёт: {score}\n"
        f"Модель NeuroBet хочет ставить на: {market_prefix} / {label} "
        f"(кэф {coeff}, вероятность модели {win_prob}%, сторона-намёк: {side}).\n\n"
        "Найди свежие прогнозы букмекеров/аналитиков, форму и статистику команд "
        "(последние матчи, травмы/новости если есть).\n"
        "Верни ТОЛЬКО JSON без markdown:\n"
        "{"
        '"lean":"home|away|draw|over|under|unknown",'
        '"confidence":0.0,'
        '"supports_bet":true,'
        '"notes":"2-4 предложения на русском"'
        "}\n"
        "supports_bet=true если внешние источники скорее поддерживают ставку модели; "
        "false если противоречат; null если данных мало. confidence от 0 до 1."
    )
    data = ask_json(prompt, search=True, timeout=35.0)
    if not data:
        return None

    lean = str(data.get("lean") or "unknown").lower().strip()
    try:
        confidence = float(data.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))
    supports = data.get("supports_bet")
    if supports is not None:
        supports = bool(supports)
    notes = str(data.get("notes") or "").strip()[:800]

    result = {
        "lean": lean,
        "confidence": round(confidence, 3),
        "supports_bet": supports,
        "notes": notes,
        "fetched_at": datetime.now(MOSCOW_TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "source": "deepseek_web_search",
    }
    # Live matches rarely last >3h; keep one search per event across scrape cycles.
    _cache_set(cache_key, result, ttl_seconds=3 * 3600)
    return result


def enrich_candidates_with_llm(
    candidates: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Attach llm_context to candidates (max N fresh web-searches per cycle).
    If veto is on and LLM confidently disagrees, drop the candidate and log.
    """
    if not candidates or not match_context_enabled():
        return candidates

    kept: List[Dict[str, Any]] = []
    fresh_searches = 0
    max_fresh = int(LLM_MAX_CONTEXT_PER_CYCLE)

    for c in candidates:
        event_id = c.get("event_id")
        cache_key = f"match_ctx:{event_id}"
        ctx = _cache_get(cache_key)
        if ctx is None and fresh_searches < max_fresh:
            ctx = fetch_match_context(c)
            fresh_searches += 1
        elif ctx is None:
            # Budget exhausted this cycle — leave without context; next cycle may fill.
            kept.append(c)
            continue

        if ctx:
            c = {**c, "llm_context": ctx}

        if veto_enabled() and ctx:
            supports = ctx.get("supports_bet")
            conf = float(ctx.get("confidence") or 0.0)
            if supports is False and conf >= float(LLM_VETO_MIN_CONFIDENCE):
                try:
                    from app.neuralbet.pipeline import add_ai_log

                    add_ai_log(
                        "BANKROLL",
                        f"LLM veto: event {event_id} «{c.get('match_name','')}» "
                        f"{c.get('label','')} — confidence={conf:.2f}, "
                        f"notes={(ctx.get('notes') or '')[:160]}",
                        level="WARNING",
                    )
                except Exception:
                    pass
                continue
        kept.append(c)

    return kept


def generate_rationale(
    prediction_or_candidate: Dict[str, Any],
    *,
    llm_context: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    if not llm_is_enabled():
        return None

    ctx = llm_context or prediction_or_candidate.get("llm_context") or {}
    ctx_notes = (ctx.get("notes") or "")[:400]
    prompt = (
        "Кратко (1–2 предложения на русском) объясни, почему модель NeuroBet ставит "
        "на этот исход. Без Markdown, без JSON.\n"
        f"Матч: {prediction_or_candidate.get('match_name') or ''} "
        f"({prediction_or_candidate.get('team_1','')} vs {prediction_or_candidate.get('team_2','')})\n"
        f"Спорт: {prediction_or_candidate.get('sport') or ''}\n"
        f"Счёт: {prediction_or_candidate.get('score') or ''}\n"
        f"Исход: {prediction_or_candidate.get('market_prefix') or ''} / "
        f"{prediction_or_candidate.get('label') or ''}\n"
        f"Кэф: {prediction_or_candidate.get('coeff') or prediction_or_candidate.get('coefficient')}\n"
        f"Вероятность модели: {prediction_or_candidate.get('win_probability')}%\n"
        f"EV: {prediction_or_candidate.get('expected_roi')}%\n"
        f"Уверенность decision-head: {prediction_or_candidate.get('decision_confidence')}\n"
        f"Внешний контекст (если есть): {ctx_notes or 'нет'}"
    )
    text = ask_text(prompt, search=False, timeout=25.0)
    if not text:
        return None
    return text.replace("\n", " ").strip()[:500]


def attach_rationales(
    predictions: List[Dict[str, Any]],
    candidates: List[Dict[str, Any]],
) -> None:
    """
    Mutates `predictions` in place: fills llm_rationale / llm_context for up to
    LLM_MAX_RATIONALE_PER_CYCLE live candidates (predicted_win bets that passed gates).
    """
    if not llm_is_enabled() or not candidates:
        return

    by_key = {
        (
            p["event_id"],
            p["factor_id"],
            str(p.get("parameter", "")),
            p.get("market_prefix") or "",
        ): p
        for p in predictions
    }

    # Prefer strongest EV candidates; one rationale call each.
    ranked = sorted(candidates, key=lambda c: float(c.get("expected_roi") or 0), reverse=True)
    budget = int(LLM_MAX_RATIONALE_PER_CYCLE)
    used = 0
    for c in ranked:
        if used >= budget:
            break
        key = (
            c["event_id"],
            c["factor_id"],
            str(c.get("parameter", "")),
            c.get("market_prefix") or "",
        )
        p = by_key.get(key)
        if p is None:
            continue
        ctx = c.get("llm_context")
        if ctx:
            p["llm_context"] = ctx
            # Propagate context to other markets of the same event that already
            # have a prediction row — cheap, no extra LLM call.
            for other in predictions:
                if other["event_id"] == c["event_id"] and not other.get("llm_context"):
                    other["llm_context"] = ctx
        rationale = generate_rationale(c, llm_context=ctx)
        if rationale:
            p["llm_rationale"] = rationale
            used += 1


# ---------------------------------------------------------------------------
# Admin digest
# ---------------------------------------------------------------------------

def _load_digest_store() -> Dict[str, Any]:
    if not os.path.exists(DIGEST_PATH):
        return {"latest": None, "history": []}
    try:
        with open(DIGEST_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {"latest": None, "history": []}


def _save_digest_store(store: Dict[str, Any]) -> None:
    os.makedirs(MODEL_DIR, exist_ok=True)
    tmp = DIGEST_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=2)
    os.replace(tmp, DIGEST_PATH)


def get_llm_digest() -> Dict[str, Any]:
    store = _load_digest_store()
    return {
        "status": "success",
        "enabled": llm_is_enabled(),
        "latest": store.get("latest"),
        "history": store.get("history") or [],
    }


def run_llm_digest() -> Dict[str, Any]:
    """Build a Russian digest of recent TRAINING/BANKROLL activity + health."""
    if not llm_is_enabled():
        return {"status": "skipped", "reason": "llm_disabled"}

    try:
        from app.neuralbet.pipeline import get_ai_logs, get_training_health
        from app.neuralbet import bankroll as bankroll_mod
    except Exception as e:
        return {"status": "error", "error": str(e)}

    logs = get_ai_logs()
    recent = [
        {
            "timestamp": e.get("timestamp"),
            "category": e.get("category"),
            "level": e.get("level"),
            "message": (e.get("message") or "")[:240],
        }
        for e in logs
        if e.get("category") in ("TRAINING", "BANKROLL", "SYSTEM")
    ][:40]

    health = get_training_health()
    try:
        balance = bankroll_mod.fetch_live_balance()
    except Exception:
        balance = None

    prompt = (
        "Ты дежурный аналитик NeuroBet. По логам и health ниже напиши дайджест на русском "
        "(6–12 предложений): что происходило с обучением и bankroll, есть ли тревоги "
        "(overfitting / quality gate / reject streak), что делать дальше. Без Markdown.\n\n"
        f"training_health: {json.dumps(health, ensure_ascii=False)[:3500]}\n"
        f"live_available_balance: {balance}\n"
        f"recent_logs: {json.dumps(recent, ensure_ascii=False)[:6000]}"
    )
    text = ask_text(prompt, search=False, timeout=70.0)
    if not text:
        return {"status": "error", "error": "empty_llm_response"}

    entry = {
        "generated_at": datetime.now(MOSCOW_TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "text": text.strip(),
        "health_status": (health or {}).get("status"),
        "live_balance": balance,
    }
    store = _load_digest_store()
    history = list(store.get("history") or [])
    history.insert(0, entry)
    history = history[: int(LLM_DIGEST_MAX_HISTORY)]
    store = {"latest": entry, "history": history}
    try:
        _save_digest_store(store)
    except Exception as e:
        logger.error("Failed to persist llm digest: %s", e)
    try:
        from app.neuralbet.pipeline import add_ai_log

        add_ai_log("SYSTEM", f"LLM digest ready ({len(text)} chars).")
    except Exception:
        pass
    return {"status": "success", "latest": entry}
