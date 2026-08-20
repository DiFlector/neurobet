"""
Optional DeepSeek LLM layer for NeuroBet.

All entry points fail soft: missing token, disabled flags, timeouts, or parse
errors return None / empty and never raise into the training or betting loop.

Shadow mode (default): web-search decisions are logged and scored against live_bets
outcomes without blocking stakes. Hard veto stays off until manual NEURALBET_LLM_VETO=1
or auto-gate (shadow proves model+veto beats model-only).
"""

from __future__ import annotations

import json
import logging
import os
import random
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.config import (
    DEEPSEEK_TOKEN,
    LLM_ASYNC,
    LLM_DIGEST_HOURS,
    LLM_DIGEST_MAX_HISTORY,
    LLM_ENABLED,
    LLM_MATCH_CONTEXT,
    LLM_MATCH_CONTEXT_SPORTS,
    LLM_MAX_CONTEXT_PER_CYCLE,
    LLM_MAX_RATIONALE_PER_CYCLE,
    LLM_MIN_INTERVAL_SECONDS,
    LLM_SHADOW,
    LLM_SHADOW_MAX_DECISIONS,
    LLM_VETO,
    LLM_VETO_AUTO,
    LLM_VETO_AUTO_MIN_SETTLED,
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
_SHADOW_LOCK = threading.Lock()
_ASYNC_LOCK = threading.Lock()
_async_busy = False
_auto_veto_cache: tuple[float, bool] = (0.0, False)
_AUTO_VETO_TTL_SEC = 300.0

DIGEST_PATH = os.path.join(MODEL_DIR, "llm_digest.json")
SHADOW_PATH = os.path.join(MODEL_DIR, "llm_shadow.json")


def llm_is_enabled() -> bool:
    return bool(LLM_ENABLED) and bool(DEEPSEEK_TOKEN and DEEPSEEK_TOKEN.strip())


def match_context_enabled() -> bool:
    return llm_is_enabled() and bool(LLM_MATCH_CONTEXT)


def shadow_enabled() -> bool:
    return llm_is_enabled() and bool(LLM_SHADOW)


def _auto_veto_eligible_cached() -> bool:
    global _auto_veto_cache
    now = time.monotonic()
    cached_at, eligible = _auto_veto_cache
    if now - cached_at < _AUTO_VETO_TTL_SEC:
        return eligible
    report = get_llm_shadow_report(refresh_outcomes=False)
    eligible = bool((report.get("veto_auto") or {}).get("eligible"))
    _auto_veto_cache = (now, eligible)
    return eligible


def veto_enabled() -> bool:
    """Hard veto in the live place path (manual flag or auto-gate)."""
    if not match_context_enabled():
        return False
    if bool(LLM_VETO):
        return True
    if bool(LLM_VETO_AUTO):
        return _auto_veto_eligible_cached()
    return False


def digest_hours() -> float:
    return float(LLM_DIGEST_HOURS)


def sport_allows_match_context(sport: Optional[str]) -> bool:
    if LLM_MATCH_CONTEXT_SPORTS is None:
        return True
    top = (sport or "").split("/")[0].strip().lower()
    return top in LLM_MATCH_CONTEXT_SPORTS


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
) -> Tuple[Optional[str], Optional[str]]:
    """Returns (text, null_reason). null_reason set when text is None."""
    if not llm_is_enabled():
        return None, "disabled"

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
        from app.deepseek.client import _sanitize_stream_text

        cleaned = _sanitize_stream_text(text or "") or None
        if not cleaned:
            return None, "empty_response"
        return cleaned, None
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
        return None, "timeout"
    except Exception as e:
        logger.warning("DeepSeek call failed: %s", e)
        try:
            from app.neuralbet.pipeline import add_ai_log

            add_ai_log("SYSTEM", f"DeepSeek error: {e}", level="WARNING")
        except Exception:
            pass
        return None, "error"


def ask_text(
    prompt: str,
    *,
    search: bool = False,
    thinking: bool = False,
    timeout: float = 45.0,
) -> Optional[str]:
    text, _reason = _call_send(prompt, search=search, thinking=thinking, timeout=timeout)
    return text


def ask_json(
    prompt: str,
    *,
    search: bool = False,
    thinking: bool = False,
    timeout: float = 45.0,
    retry: bool = True,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Returns (parsed_dict, null_reason)."""
    text, reason = _call_send(prompt, search=search, thinking=thinking, timeout=timeout)
    if not text:
        return None, reason or "empty_response"
    parsed = _parse_json(text)
    if parsed is not None:
        return parsed, None
    if not retry:
        return None, "parse_error"
    retry_prompt = (
        prompt
        + "\n\nОтвет должен быть ТОЛЬКО валидным JSON-объектом без markdown и без пояснений."
    )
    text2, reason2 = _call_send(
        retry_prompt, search=search, thinking=thinking, timeout=timeout
    )
    if not text2:
        return None, reason2 or "parse_error"
    parsed2 = _parse_json(text2)
    return (parsed2, None) if parsed2 is not None else (None, "parse_error")


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


def _would_veto(ctx: Optional[Dict[str, Any]]) -> bool:
    if not ctx:
        return False
    supports = ctx.get("supports_bet")
    conf = float(ctx.get("confidence") or 0.0)
    return supports is False and conf >= float(LLM_VETO_MIN_CONFIDENCE)


def fetch_match_context(
    candidate: Dict[str, Any],
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Web-search context for one live stake candidate. Cached by event_id.

    Returns (ctx, null_reason). null_reason is set when ctx is None.
    """
    if not match_context_enabled():
        return None, "disabled"

    sport = candidate.get("sport") or ""
    if not sport_allows_match_context(sport):
        return None, "sport_skipped"

    event_id = candidate.get("event_id")
    cache_key = f"match_ctx:{event_id}"
    cached = _cache_get(cache_key)
    if cached is not None:
        if isinstance(cached, dict) and cached.get("_null_reason"):
            return None, str(cached["_null_reason"])
        return cached, None

    team_1 = candidate.get("team_1") or ""
    team_2 = candidate.get("team_2") or ""
    match_name = candidate.get("match_name") or f"{team_1} vs {team_2}"
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
    data, reason = ask_json(prompt, search=True, timeout=35.0)
    if not data:
        null_reason = reason or "empty_response"
        _cache_set(cache_key, {"_null_reason": null_reason}, ttl_seconds=30 * 60)
        return None, null_reason

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
        "null_reason": None,
    }
    _cache_set(cache_key, result, ttl_seconds=3 * 3600)
    return result, None


def _candidate_key(c: Dict[str, Any]) -> Tuple[Any, Any, str, str]:
    return (
        c.get("event_id"),
        c.get("factor_id"),
        str(c.get("parameter", "")),
        c.get("market_prefix") or "",
    )


def record_shadow_decision(
    candidate: Dict[str, Any],
    *,
    ctx: Optional[Dict[str, Any]],
    null_reason: Optional[str],
    placed: bool,
    live_bet_id: Optional[int] = None,
) -> None:
    if not shadow_enabled():
        return
    decision = {
        "id": str(uuid.uuid4()),
        "recorded_at": datetime.now(MOSCOW_TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "event_id": candidate.get("event_id"),
        "factor_id": candidate.get("factor_id"),
        "market_prefix": candidate.get("market_prefix") or "",
        "parameter": str(candidate.get("parameter", "")),
        "label": candidate.get("label") or "",
        "match_name": candidate.get("match_name") or "",
        "sport": candidate.get("sport") or "",
        "coeff": float(candidate.get("coeff") or candidate.get("coefficient") or 0.0),
        "win_probability": candidate.get("win_probability"),
        "expected_roi": candidate.get("expected_roi"),
        "placed": bool(placed),
        "live_bet_id": live_bet_id,
        "llm": {
            "lean": (ctx or {}).get("lean"),
            "confidence": (ctx or {}).get("confidence"),
            "supports_bet": (ctx or {}).get("supports_bet"),
            "notes": ((ctx or {}).get("notes") or "")[:400] or None,
            "null_reason": null_reason,
            "would_veto": _would_veto(ctx),
        },
        "outcome": None,
        "pnl_unit": None,
    }
    with _SHADOW_LOCK:
        store = _load_shadow_store()
        decisions = list(store.get("decisions") or [])
        decisions.insert(0, decision)
        max_n = max(100, int(LLM_SHADOW_MAX_DECISIONS))
        store["decisions"] = decisions[:max_n]
        store["updated_at"] = decision["recorded_at"]
        try:
            _save_shadow_store(store)
        except Exception as e:
            logger.error("Failed to persist llm shadow: %s", e)


def enrich_candidates_with_llm(
    candidates: List[Dict[str, Any]],
    *,
    apply_veto: bool = False,
    placed_keys: Optional[set] = None,
) -> List[Dict[str, Any]]:
    """
    Attach llm_context to candidates (max N fresh web-searches per cycle).
    Always shadow-logs when LLM_SHADOW is on. Hard-drops only when apply_veto=True.
    """
    if not candidates or not match_context_enabled():
        return candidates

    kept: List[Dict[str, Any]] = []
    fresh_searches = 0
    max_fresh = int(LLM_MAX_CONTEXT_PER_CYCLE)
    placed_keys = placed_keys or set()

    for c in candidates:
        event_id = c.get("event_id")
        sport = c.get("sport") or ""
        null_reason: Optional[str] = None
        ctx: Optional[Dict[str, Any]] = None
        attempted = False

        if not sport_allows_match_context(sport):
            null_reason = "sport_skipped"
        else:
            cache_key = f"match_ctx:{event_id}"
            cached = _cache_get(cache_key)
            if cached is not None:
                attempted = True
                if isinstance(cached, dict) and cached.get("_null_reason"):
                    null_reason = str(cached["_null_reason"])
                    ctx = None
                else:
                    ctx = cached
            elif fresh_searches < max_fresh:
                ctx, null_reason = fetch_match_context(c)
                fresh_searches += 1
                attempted = True
            else:
                null_reason = "budget_exhausted"

        if ctx:
            c = {**c, "llm_context": ctx}

        key = _candidate_key(c)
        # Don't flood shadow with NT sport_skipped / budget leftovers every cycle.
        if shadow_enabled() and (attempted or key in placed_keys):
            record_shadow_decision(
                c,
                ctx=ctx,
                null_reason=null_reason,
                placed=key in placed_keys,
            )

        if apply_veto and _would_veto(ctx):
            try:
                from app.neuralbet.pipeline import add_ai_log

                add_ai_log(
                    "BANKROLL",
                    f"LLM veto: event {event_id} «{c.get('match_name','')}» "
                    f"{c.get('label','')} — confidence={float((ctx or {}).get('confidence') or 0):.2f}, "
                    f"notes={((ctx or {}).get('notes') or '')[:160]}",
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
            for other in predictions:
                if other["event_id"] == c["event_id"] and not other.get("llm_context"):
                    other["llm_context"] = ctx
        rationale = generate_rationale(c, llm_context=ctx)
        if rationale:
            p["llm_rationale"] = rationale
            used += 1


def run_llm_post_cycle(
    candidates: List[Dict[str, Any]],
    predictions: List[Dict[str, Any]],
    timestamp_str: str,
    *,
    placed_keys: Optional[set] = None,
    apply_veto: bool = False,
) -> List[Dict[str, Any]]:
    """Enrich + rationales + persist prediction LLM fields. Returns kept candidates."""
    if not candidates or not llm_is_enabled():
        return candidates
    kept = candidates
    try:
        if match_context_enabled():
            kept = enrich_candidates_with_llm(
                candidates,
                apply_veto=apply_veto,
                placed_keys=placed_keys,
            )
        attach_rationales(predictions, kept if kept else candidates)
        # Persist only rows that gained LLM fields.
        to_save = [
            p
            for p in predictions
            if p.get("llm_context") is not None or p.get("llm_rationale")
        ]
        if to_save:
            from app.core.database import save_ai_predictions

            save_ai_predictions(to_save, timestamp_str)
    except Exception as e:
        logger.warning("LLM post-cycle failed: %s", e)
        try:
            from app.neuralbet.pipeline import add_ai_log

            add_ai_log("SYSTEM", f"LLM enrich skipped: {e}", level="WARNING")
        except Exception:
            pass
    return kept


def schedule_llm_post_cycle(
    candidates: List[Dict[str, Any]],
    predictions: List[Dict[str, Any]],
    timestamp_str: str,
    *,
    placed_keys: Optional[set] = None,
) -> None:
    """Fire-and-forget LLM enrich after bankroll place (does not block staking)."""
    global _async_busy
    if not candidates or not llm_is_enabled():
        return
    if not bool(LLM_ASYNC):
        run_llm_post_cycle(
            candidates, predictions, timestamp_str, placed_keys=placed_keys
        )
        return

    with _ASYNC_LOCK:
        if _async_busy:
            try:
                from app.neuralbet.pipeline import add_ai_log

                add_ai_log(
                    "SYSTEM",
                    "LLM async enrich skipped — previous cycle still running.",
                    level="INFO",
                )
            except Exception:
                pass
            return
        _async_busy = True

    # Copy so the inference thread can mutate originals safely.
    cands = [dict(c) for c in candidates]
    preds = [dict(p) for p in predictions]
    keys = set(placed_keys or set())

    def _worker() -> None:
        global _async_busy
        try:
            run_llm_post_cycle(cands, preds, timestamp_str, placed_keys=keys)
        finally:
            with _ASYNC_LOCK:
                _async_busy = False

    threading.Thread(target=_worker, name="llm-post-cycle", daemon=True).start()


# ---------------------------------------------------------------------------
# Shadow store + report (model-only vs model+LLM veto)
# ---------------------------------------------------------------------------

def _load_shadow_store() -> Dict[str, Any]:
    if not os.path.exists(SHADOW_PATH):
        return {"decisions": [], "updated_at": None}
    try:
        with open(SHADOW_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {"decisions": [], "updated_at": None}


def _save_shadow_store(store: Dict[str, Any]) -> None:
    os.makedirs(MODEL_DIR, exist_ok=True)
    tmp = SHADOW_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=2)
    os.replace(tmp, SHADOW_PATH)


def _fetch_live_bet_outcomes(
    keys: List[Tuple[Any, Any, str, str]],
) -> Dict[Tuple[Any, Any, str, str], Dict[str, Any]]:
    if not keys:
        return {}
    try:
        from app.core.database import get_finished_connection, release_connection
    except Exception:
        return {}

    out: Dict[Tuple[Any, Any, str, str], Dict[str, Any]] = {}
    conn = None
    try:
        conn = get_finished_connection()
        cur = conn.cursor()
        # Bound lookup: recent live bets only.
        cur.execute(
            """
            SELECT id, event_id, factor_id, market_prefix, parameter, status,
                   stake, payout, coefficient, placed_at
            FROM live_bets
            ORDER BY id DESC
            LIMIT 2000
            """
        )
        for row in cur.fetchall():
            key = (
                row["event_id"],
                row["factor_id"],
                str(row.get("parameter") or ""),
                row.get("market_prefix") or "",
            )
            if key in out:
                continue
            out[key] = dict(row)
    except Exception as e:
        logger.warning("Shadow outcome lookup failed: %s", e)
    finally:
        if conn is not None:
            try:
                release_connection(conn)
            except Exception:
                pass
    return out


def _unit_pnl(status: str, coeff: float) -> Optional[float]:
    s = (status or "").lower()
    if s == "won":
        return float(coeff) - 1.0
    if s == "lost":
        return -1.0
    if s in ("void", "cancelled"):
        return 0.0
    return None


def _portfolio_stats(pnls: List[float], seed: int = 42) -> Dict[str, Any]:
    n = len(pnls)
    if n == 0:
        return {
            "bets": 0,
            "wins": 0,
            "losses": 0,
            "win_rate_pct": None,
            "roi_pct": None,
            "roi_pct_lo": None,
            "roi_pct_hi": None,
        }
    wins = sum(1 for x in pnls if x > 0)
    losses = sum(1 for x in pnls if x < 0)
    mean = sum(pnls) / n
    # Bootstrap CI on mean unit ROI.
    rng = random.Random(seed)
    boots: List[float] = []
    for _ in range(min(500, max(50, n * 5))):
        sample = [pnls[rng.randrange(n)] for _ in range(n)]
        boots.append(sum(sample) / n)
    boots.sort()
    lo = boots[int(0.025 * (len(boots) - 1))]
    hi = boots[int(0.975 * (len(boots) - 1))]
    return {
        "bets": n,
        "wins": wins,
        "losses": losses,
        "win_rate_pct": round(100.0 * wins / max(1, wins + losses), 1),
        "roi_pct": round(100.0 * mean, 1),
        "roi_pct_lo": round(100.0 * lo, 1),
        "roi_pct_hi": round(100.0 * hi, 1),
    }


def _refresh_shadow_outcomes(store: Dict[str, Any]) -> Dict[str, Any]:
    decisions = list(store.get("decisions") or [])
    keys = [
        (
            d.get("event_id"),
            d.get("factor_id"),
            str(d.get("parameter", "")),
            d.get("market_prefix") or "",
        )
        for d in decisions
    ]
    outcomes = _fetch_live_bet_outcomes(keys)
    changed = False
    for d in decisions:
        key = (
            d.get("event_id"),
            d.get("factor_id"),
            str(d.get("parameter", "")),
            d.get("market_prefix") or "",
        )
        row = outcomes.get(key)
        if not row:
            continue
        status = (row.get("status") or "").lower()
        d["live_bet_id"] = row.get("id")
        if not d.get("placed") and status:
            d["placed"] = True
            changed = True
        if d.get("outcome") != status:
            d["outcome"] = status
            changed = True
        pnl = _unit_pnl(status, float(row.get("coefficient") or d.get("coeff") or 0.0))
        if pnl is not None and d.get("pnl_unit") != pnl:
            d["pnl_unit"] = pnl
            changed = True
    if changed:
        store["decisions"] = decisions
        store["updated_at"] = datetime.now(MOSCOW_TZ).strftime("%Y-%m-%d %H:%M:%S")
        try:
            _save_shadow_store(store)
        except Exception as e:
            logger.error("Failed to update shadow outcomes: %s", e)
    return store


def get_llm_shadow_report(*, refresh_outcomes: bool = True) -> Dict[str, Any]:
    """Compare model-only vs model+LLM-veto on accumulated shadow decisions."""
    with _SHADOW_LOCK:
        store = _load_shadow_store()
        if refresh_outcomes:
            store = _refresh_shadow_outcomes(store)
        decisions = list(store.get("decisions") or [])

    null_counts: Dict[str, int] = {}
    for d in decisions:
        reason = ((d.get("llm") or {}).get("null_reason")) or (
            "ok" if (d.get("llm") or {}).get("supports_bet") is not None else "unknown"
        )
        null_counts[reason] = null_counts.get(reason, 0) + 1

    settled = [
        d
        for d in decisions
        if d.get("pnl_unit") is not None
        and (d.get("outcome") or "").lower() in ("won", "lost")
    ]
    model_pnls = [float(d["pnl_unit"]) for d in settled]
    kept_pnls = [
        float(d["pnl_unit"])
        for d in settled
        if not bool((d.get("llm") or {}).get("would_veto"))
    ]
    vetoed = [d for d in settled if bool((d.get("llm") or {}).get("would_veto"))]
    vetoed_pnls = [float(d["pnl_unit"]) for d in vetoed]

    model_only = _portfolio_stats(model_pnls, seed=42)
    with_veto = _portfolio_stats(kept_pnls, seed=43)
    vetoed_cf = _portfolio_stats(vetoed_pnls, seed=44)

    min_settled = max(40, int(LLM_VETO_AUTO_MIN_SETTLED))
    # Eligible when enough settled bets, veto removes net-negative bets, and
    # filtered book improves ROI + win-rate with CI lo still > 0 when sample allows.
    eligible = False
    eligible_reasons: List[str] = []
    if model_only["bets"] < min_settled:
        eligible_reasons.append(
            f"need ≥{min_settled} settled shadow bets (have {model_only['bets']})"
        )
    else:
        roi_ok = (
            with_veto["roi_pct"] is not None
            and model_only["roi_pct"] is not None
            and with_veto["roi_pct"] > model_only["roi_pct"]
        )
        wr_ok = (
            with_veto["win_rate_pct"] is not None
            and model_only["win_rate_pct"] is not None
            and with_veto["win_rate_pct"] >= model_only["win_rate_pct"]
        )
        ci_ok = with_veto["roi_pct_lo"] is not None and with_veto["roi_pct_lo"] > 0
        veto_hurt = (
            vetoed_cf["bets"] >= 10
            and vetoed_cf["roi_pct"] is not None
            and vetoed_cf["roi_pct"] < 0
        )
        if not roi_ok:
            eligible_reasons.append("with_veto ROI not better than model-only")
        if not wr_ok:
            eligible_reasons.append("with_veto win-rate not ≥ model-only")
        if not ci_ok:
            eligible_reasons.append("with_veto roi_pct_lo ≤ 0")
        if not veto_hurt:
            eligible_reasons.append(
                "vetoed counterfactual not clearly negative (need ≥10 vetoed losses)"
            )
        eligible = roi_ok and wr_ok and ci_ok and veto_hurt

    if eligible:
        recommendation = "enable_veto"
    elif model_only["bets"] < min_settled:
        recommendation = "keep_shadow"
    elif (
        with_veto["roi_pct"] is not None
        and model_only["roi_pct"] is not None
        and with_veto["roi_pct"] < model_only["roi_pct"]
    ):
        recommendation = "disable_veto_keep_shadow"
    else:
        recommendation = "keep_shadow"

    sports_cfg = (
        "*"
        if LLM_MATCH_CONTEXT_SPORTS is None
        else sorted(LLM_MATCH_CONTEXT_SPORTS)
    )

    return {
        "status": "success",
        "enabled": llm_is_enabled(),
        "shadow_enabled": shadow_enabled(),
        "match_context_enabled": match_context_enabled(),
        "match_context_sports": sports_cfg,
        "async_enabled": bool(LLM_ASYNC),
        "veto_manual": bool(LLM_VETO),
        "veto_active": veto_enabled(),
        "veto_auto": {
            "enabled": bool(LLM_VETO_AUTO),
            "eligible": eligible,
            "min_settled": min_settled,
            "reasons": eligible_reasons,
            "min_confidence": float(LLM_VETO_MIN_CONFIDENCE),
        },
        "decisions_total": len(decisions),
        "null_reasons": null_counts,
        "model_only": model_only,
        "with_veto": with_veto,
        "vetoed_counterfactual": vetoed_cf,
        "recommendation": recommendation,
        "updated_at": store.get("updated_at"),
        "recent": decisions[:20],
    }


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

    shadow = get_llm_shadow_report(refresh_outcomes=True)
    shadow_compact = {
        "recommendation": shadow.get("recommendation"),
        "veto_active": shadow.get("veto_active"),
        "veto_auto": shadow.get("veto_auto"),
        "model_only": shadow.get("model_only"),
        "with_veto": shadow.get("with_veto"),
        "null_reasons": shadow.get("null_reasons"),
    }

    prompt = (
        "Ты дежурный аналитик NeuroBet. По логам и health ниже напиши дайджест на русском "
        "(6–12 предложений): что происходило с обучением и bankroll, есть ли тревоги "
        "(overfitting / quality gate / reject streak / LLM shadow), что делать дальше. "
        "Без Markdown.\n\n"
        f"training_health: {json.dumps(health, ensure_ascii=False)[:3500]}\n"
        f"live_available_balance: {balance}\n"
        f"llm_shadow: {json.dumps(shadow_compact, ensure_ascii=False)[:2500]}\n"
        f"recent_logs: {json.dumps(recent, ensure_ascii=False)[:5500]}"
    )
    text = ask_text(prompt, search=False, timeout=70.0)
    if not text:
        return {"status": "error", "error": "empty_llm_response"}

    entry = {
        "generated_at": datetime.now(MOSCOW_TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "text": text.strip(),
        "health_status": (health or {}).get("status"),
        "live_balance": balance,
        "llm_shadow_recommendation": shadow.get("recommendation"),
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
