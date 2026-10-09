"""Candidate Scheduling & Cost Control Engine according to Architecture Section 33 and Roadmap Phase 17."""

import hashlib
import heapq
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from contracts.decisions import CandidateItem, DecisionResult, LLMStructuredVerdict
from contracts.scheduling import (
    CandidatePriorityItem,
    CandidateSchedulerConfig,
    CandidateScoreBreakdown,
    QueueStatusReport,
)
from sqlalchemy.orm import Session

logger = logging.getLogger("bankroll.scheduler")


class CandidateScorer:
    """
    Computes a composite priority score combining edge, statistical confidence,
    market liquidity, and recency of match observation.
    """

    @staticmethod
    def calculate_score(
        candidate: CandidateItem,
        config: CandidateSchedulerConfig,
        seconds_since_observation: float = 0.0,
    ) -> CandidateScoreBreakdown:
        # 1. Edge score: maps [0.0, 0.15] edge to [0.0, 1.0]
        edge_normalized = max(0.0, min(1.0, candidate.edge / 0.15))

        # 2. Confidence score: maps [0.50, 1.00] confidence to [0.0, 1.0]
        conf_normalized = max(0.0, min(1.0, (candidate.confidence - 0.50) / 0.50))

        # 3. Liquidity score: higher for main match winner markets
        if candidate.market in ("match_winner", "winner", "1x2"):
            liquidity = 1.0
        elif "handicap" in candidate.market or "total" in candidate.market:
            liquidity = 0.85
        else:
            liquidity = 0.70

        # 4. Freshness score: scales down for stale snapshots (>30s)
        freshness = max(0.0, min(1.0, 1.0 - (seconds_since_observation / 30.0)))

        # Weighted composite score
        composite = (
            config.weight_edge * edge_normalized
            + config.weight_confidence * conf_normalized
            + config.weight_liquidity * liquidity
            + config.weight_freshness * freshness
        )
        composite = round(max(0.0, min(1.0, composite)), 4)

        return CandidateScoreBreakdown(
            edge_score=round(edge_normalized, 4),
            confidence_score=round(conf_normalized, 4),
            liquidity_score=round(liquidity, 4),
            freshness_score=round(freshness, 4),
            composite_score=composite,
        )


class CooldownManager:
    """
    Thread-safe per-event cooldown tracker to prevent querying web research
    or local LLM repeatedly within a narrow time window.
    """

    def __init__(self, research_cooldown_seconds: int = 180, llm_cooldown_seconds: int = 60):
        self.research_cooldown_seconds = research_cooldown_seconds
        self.llm_cooldown_seconds = llm_cooldown_seconds
        self._last_research_time: Dict[str, float] = {}
        self._last_llm_time: Dict[str, float] = {}
        self._lock = threading.Lock()

    def is_research_in_cooldown(self, event_id: str) -> bool:
        with self._lock:
            last = self._last_research_time.get(event_id)
            if not last:
                return False
            return (time.time() - last) < self.research_cooldown_seconds

    def is_llm_in_cooldown(self, event_id: str) -> bool:
        with self._lock:
            last = self._last_llm_time.get(event_id)
            if not last:
                return False
            return (time.time() - last) < self.llm_cooldown_seconds

    def record_research(self, event_id: str) -> None:
        with self._lock:
            self._last_research_time[event_id] = time.time()

    def record_llm(self, event_id: str) -> None:
        with self._lock:
            self._last_llm_time[event_id] = time.time()

    def get_active_cooldowns(self) -> Tuple[int, int]:
        now = time.time()
        with self._lock:
            active_res = sum(
                1 for t in self._last_research_time.values() if (now - t) < self.research_cooldown_seconds
            )
            active_llm = sum(
                1 for t in self._last_llm_time.values() if (now - t) < self.llm_cooldown_seconds
            )
            return active_res, active_llm

    def clear(self) -> None:
        with self._lock:
            self._last_research_time.clear()
            self._last_llm_time.clear()


class SnapshotLLMCache:
    """
    Deterministic cache for LLM verdicts based on event state snapshots.
    If match state, selection, and odds have not evolved, avoids redundant inference.
    """

    def __init__(self, ttl_seconds: int = 300):
        self.ttl_seconds = ttl_seconds
        self._cache: Dict[str, Tuple[LLMStructuredVerdict, float]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def generate_state_hash(
        candidate: CandidateItem,
        snapshot_version: Optional[str] = None,
        score_state: Optional[str] = None,
    ) -> str:
        key_raw = f"{candidate.event_id}:{snapshot_version or 'v1'}:{score_state or ''}:{candidate.selection}:{candidate.odds:.2f}:{candidate.model_probability:.2f}"
        return hashlib.sha256(key_raw.encode("utf-8")).hexdigest()

    def get(self, state_hash: str) -> Optional[LLMStructuredVerdict]:
        now = time.time()
        with self._lock:
            if state_hash in self._cache:
                verdict, exp = self._cache[state_hash]
                if now < exp:
                    return verdict
                else:
                    del self._cache[state_hash]
            return None

    def set(self, state_hash: str, verdict: LLMStructuredVerdict) -> None:
        with self._lock:
            self._cache[state_hash] = (verdict, time.time() + self.ttl_seconds)

    def size(self) -> int:
        with self._lock:
            return len(self._cache)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()


class CandidatePriorityQueue:
    """
    In-memory thread-safe priority queue (max-heap) for candidates.
    Orders candidates strictly by composite score descending.
    Prunes the lowest scoring items when capacity is reached.
    """

    def __init__(self, max_capacity: int = 100):
        self.max_capacity = max_capacity
        self._heap: List[Tuple[float, int, CandidatePriorityItem]] = []
        self._counter = 0
        self._lock = threading.Lock()

    def push(self, item: CandidatePriorityItem) -> bool:
        """
        Pushes a candidate item. If queue is full and item has a higher score
        than the lowest queued item, the lowest item is dropped.
        """
        score = item.score_breakdown.composite_score
        with self._lock:
            self._counter += 1
            # Note: Python heapq is a min-heap. We push (-score) to act as max-heap.
            if len(self._heap) >= self.max_capacity:
                # Find current worst element (highest -score, i.e. lowest score)
                worst_neg_score = max(x[0] for x in self._heap)
                worst_score = -worst_neg_score
                if score <= worst_score:
                    # New item is worse than or equal to current worst; reject
                    return False
                # Remove the worst item
                worst_idx = next(i for i, x in enumerate(self._heap) if x[0] == worst_neg_score)
                self._heap.pop(worst_idx)
                heapq.heapify(self._heap)

            heapq.heappush(self._heap, (-score, self._counter, item))
            return True

    def pop(self) -> Optional[CandidatePriorityItem]:
        with self._lock:
            if not self._heap:
                return None
            _, _, item = heapq.heappop(self._heap)
            return item

    def peek(self) -> Optional[CandidatePriorityItem]:
        with self._lock:
            if not self._heap:
                return None
            return self._heap[0][2]

    def size(self) -> int:
        with self._lock:
            return len(self._heap)

    def items(self) -> List[CandidatePriorityItem]:
        with self._lock:
            # Sort items by composite score descending
            sorted_entries = sorted(self._heap, key=lambda x: x[0])
            return [x[2] for x in sorted_entries]

    def clear(self) -> None:
        with self._lock:
            self._heap.clear()


class TokenBucketRateLimiter:
    """
    Token-bucket rate limiter to enforce max LLM requests per second.
    """

    def __init__(self, max_rps: float = 2.0, burst_size: int = 5):
        self.max_rps = max(0.1, max_rps)
        self.burst_size = burst_size
        self._tokens = float(burst_size)
        self._last_time = time.time()
        self._lock = threading.Lock()

    def acquire(self) -> bool:
        with self._lock:
            now = time.time()
            elapsed = now - self._last_time
            self._last_time = now
            # Replenish tokens
            self._tokens = min(self.burst_size, self._tokens + elapsed * self.max_rps)
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return True
            return False


class BrowserConcurrencyLimiter:
    """
    Limits concurrent browser fetch instances for web research.
    """

    def __init__(self, max_concurrency: int = 3):
        self.max_concurrency = max_concurrency
        self._semaphore = threading.BoundedSemaphore(max_concurrency)

    def acquire(self, blocking: bool = True, timeout: Optional[float] = 2.0) -> bool:
        if not blocking:
            return self._semaphore.acquire(blocking=False)
        if timeout is not None:
            return self._semaphore.acquire(blocking=True, timeout=timeout)
        return self._semaphore.acquire(blocking=True)

    def release(self) -> None:
        try:
            self._semaphore.release()
        except ValueError:
            pass


class CandidateScheduler:
    """
    Central scheduler and cost-control coordinator for live event candidate evaluation.
    Enforces candidate score hurdles, priority queue ordering, per-event cooldowns,
    snapshot-based caching, and LLM/browser rate limits.
    """

    def __init__(self, config: Optional[CandidateSchedulerConfig] = None):
        self.config = config or CandidateSchedulerConfig()
        self.scorer = CandidateScorer()
        self.cooldown_manager = CooldownManager(
            research_cooldown_seconds=self.config.research_cooldown_seconds,
            llm_cooldown_seconds=self.config.llm_cooldown_seconds,
        )
        self.snapshot_cache = SnapshotLLMCache()
        self.priority_queue = CandidatePriorityQueue(max_capacity=self.config.max_queue_size)
        self.llm_rate_limiter = TokenBucketRateLimiter(max_rps=self.config.llm_max_rps)
        self.browser_limiter = BrowserConcurrencyLimiter(max_concurrency=self.config.browser_max_concurrency)

        # Metrics counters
        self._total_evaluated = 0
        self._total_dispatched = 0
        self._total_cooldown_skips = 0
        self._total_cache_hits = 0

    def evaluate_and_enqueue(
        self,
        candidate: CandidateItem,
        snapshot_version: Optional[str] = None,
        score_state: Optional[str] = None,
    ) -> Optional[CandidatePriorityItem]:
        """
        Scores candidate against composite hurdle and enqueues into priority queue.
        Returns CandidatePriorityItem if enqueued, or None if rejected by score hurdle.
        """
        self._total_evaluated += 1

        breakdown = self.scorer.calculate_score(candidate, self.config)
        if breakdown.composite_score < self.config.min_candidate_score:
            logger.debug(
                f"Candidate {candidate.event_id} ({candidate.selection}) score {breakdown.composite_score:.3f} below hurdle {self.config.min_candidate_score:.3f}"
            )
            return None

        if not candidate.is_shortlisted:
            candidate = candidate.model_copy(update={"is_shortlisted": True})

        state_hash = self.snapshot_cache.generate_state_hash(candidate, snapshot_version, score_state)
        priority_item = CandidatePriorityItem(
            candidate=candidate,
            score_breakdown=breakdown,
            snapshot_version=snapshot_version,
            state_hash=state_hash,
        )

        pushed = self.priority_queue.push(priority_item)
        return priority_item if pushed else None

    def dispatch_candidate(
        self,
        session: Session,
        item: CandidatePriorityItem,
        decision_pipeline: Any,
    ) -> DecisionResult:
        """
        Dispatches candidate through decision pipeline, applying snapshot cache,
        per-event cooldowns, and rate limiting.
        """
        candidate = item.candidate
        state_hash = item.state_hash or ""
        self._total_dispatched += 1

        # 1. Check snapshot LLM cache
        cached_verdict = self.snapshot_cache.get(state_hash)
        if cached_verdict:
            self._total_cache_hits += 1
            logger.info(f"Snapshot cache HIT for candidate {candidate.event_id} ({state_hash[:8]})")
            # Directly process with cached verdict without calling LLM
            class CachedLLMClient:
                def analyze(self, _):
                    return cached_verdict

            return decision_pipeline.evaluate_candidate(
                session=session,
                candidate=candidate,
                llm_client=CachedLLMClient(),
            )

        # 2. Check per-event LLM cooldown
        if self.cooldown_manager.is_llm_in_cooldown(candidate.event_id):
            self._total_cooldown_skips += 1
            logger.info(f"Event {candidate.event_id} in LLM cooldown. Falling back to ML-only or suppressing.")
            # Fall back to ML-only evaluation to save LLM budget
            ml_only_config = decision_pipeline.config.model_copy(update={"llm_enabled": False})
            from bankroll.decision_pipeline import CombinedDecisionPipeline
            ml_pipeline = CombinedDecisionPipeline(config=ml_only_config)
            return ml_pipeline.evaluate_candidate(session=session, candidate=candidate)

        # 3. Check rate limiting for LLM
        if not self.llm_rate_limiter.acquire():
            logger.warning("LLM rate limiter exceeded quota. Falling back to ML-only evaluation.")
            ml_only_config = decision_pipeline.config.model_copy(update={"llm_enabled": False})
            from bankroll.decision_pipeline import CombinedDecisionPipeline
            ml_pipeline = CombinedDecisionPipeline(config=ml_only_config)
            return ml_pipeline.evaluate_candidate(session=session, candidate=candidate)

        # 4. Check web research cooldown
        research_snippets = []
        if not self.cooldown_manager.is_research_in_cooldown(candidate.event_id):
            # Safe to research; record research timestamp
            self.cooldown_manager.record_research(candidate.event_id)
        else:
            logger.info(f"Event {candidate.event_id} in research cooldown. Skipping external fetch.")

        # 5. Execute decision pipeline
        res = decision_pipeline.evaluate_candidate(session=session, candidate=candidate)

        # Record LLM execution timestamp and cache verdict if evaluated
        self.cooldown_manager.record_llm(candidate.event_id)
        if hasattr(decision_pipeline, "_last_verdict") and decision_pipeline._last_verdict:
            self.snapshot_cache.set(state_hash, decision_pipeline._last_verdict)

        return res

    def get_status_report(self) -> QueueStatusReport:
        active_res, active_llm = self.cooldown_manager.get_active_cooldowns()
        return QueueStatusReport(
            queue_size=self.priority_queue.size(),
            max_queue_size=self.config.max_queue_size,
            active_research_cooldowns=active_res,
            active_llm_cooldowns=active_llm,
            snapshot_cache_size=self.snapshot_cache.size(),
            total_evaluated=self._total_evaluated,
            total_dispatched=self._total_dispatched,
            total_cooldown_skips=self._total_cooldown_skips,
            total_cache_hits=self._total_cache_hits,
        )
