"""Canonical stream names and Redis constants for Neurobet."""

# 15 Core System Streams
STREAM_FONBET_RAW = "fonbet.raw"
STREAM_FONBET_EVENTS = "fonbet.events"
STREAM_FONBET_STATE = "fonbet.state"
STREAM_FONBET_ODDS = "fonbet.odds"
STREAM_FONBET_RESULTS = "fonbet.results"

STREAM_FEATURES_READY = "features.ready"
STREAM_ML_PREDICTIONS = "ml.predictions"

STREAM_RESEARCH_REQUESTS = "research.requests"
STREAM_RESEARCH_RESULTS = "research.results"

STREAM_LLM_REQUESTS = "llm.requests"
STREAM_LLM_RESULTS = "llm.results"

STREAM_BET_PROPOSALS = "bet.proposals"
STREAM_BET_VALIDATED = "bet.validated"
STREAM_BET_EXECUTED = "bet.executed"
STREAM_BET_SETTLED = "bet.settled"

STREAM_TRAINING_JOBS = "training.jobs"

# Dead Letter Queue
STREAM_DEAD_LETTER = "streams.dead_letter"

ALL_CORE_STREAMS = [
    STREAM_FONBET_RAW,
    STREAM_FONBET_EVENTS,
    STREAM_FONBET_STATE,
    STREAM_FONBET_ODDS,
    STREAM_FONBET_RESULTS,
    STREAM_FEATURES_READY,
    STREAM_ML_PREDICTIONS,
    STREAM_RESEARCH_REQUESTS,
    STREAM_RESEARCH_RESULTS,
    STREAM_LLM_REQUESTS,
    STREAM_LLM_RESULTS,
    STREAM_BET_PROPOSALS,
    STREAM_BET_VALIDATED,
    STREAM_BET_EXECUTED,
    STREAM_BET_SETTLED,
    STREAM_TRAINING_JOBS,
]

ALL_STREAMS = ALL_CORE_STREAMS + [STREAM_DEAD_LETTER]

# Idempotency prefix and TTL
IDEMPOTENCY_KEY_PREFIX = "idempotency:"
DEFAULT_IDEMPOTENCY_TTL_SECONDS = 86400  # 24 hours

# Consumer group names
CG_EVENT_PROCESSOR = "cg.event_processor"
CG_NEURAL = "cg.neural"
CG_RESEARCH = "cg.research"
CG_LLM = "cg.llm"
CG_BET_MANAGER = "cg.bet_manager"
CG_WORKER = "cg.worker"
