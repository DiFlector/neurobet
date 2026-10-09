import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple


class Metric:
    """Base metric class supporting labels and thread-safe operations."""

    def __init__(self, name: str, description: str, metric_type: str):
        self.name = name
        self.description = description
        self.metric_type = metric_type
        self._lock = threading.Lock()

    def _format_labels(self, labels: Dict[str, str]) -> str:
        if not labels:
            return ""
        items = [f'{k}="{v}"' for k, v in sorted(labels.items())]
        return "{" + ",".join(items) + "}"


class Counter(Metric):
    """Monotonically increasing cumulative metric."""

    def __init__(self, name: str, description: str):
        super().__init__(name, description, "counter")
        self._values: Dict[Tuple[Tuple[str, str], ...], float] = {}

    def inc(self, amount: float = 1.0, labels: Optional[Dict[str, str]] = None):
        if amount < 0:
            raise ValueError("Counter cannot be decremented")
        key = tuple(sorted(labels.items())) if labels else ()
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + amount

    def get(self, labels: Optional[Dict[str, str]] = None) -> float:
        key = tuple(sorted(labels.items())) if labels else ()
        with self._lock:
            return self._values.get(key, 0.0)

    def render(self) -> List[str]:
        lines = [
            f"# HELP {self.name} {self.description}",
            f"# TYPE {self.name} counter",
        ]
        with self._lock:
            if not self._values:
                lines.append(f"{self.name} 0.0")
            else:
                for key, val in sorted(self._values.items()):
                    lbl_str = self._format_labels(dict(key))
                    lines.append(f"{self.name}{lbl_str} {val}")
        return lines


class Gauge(Metric):
    """Metric that represents a single numerical value that can arbitrarily go up and down."""

    def __init__(self, name: str, description: str):
        super().__init__(name, description, "gauge")
        self._values: Dict[Tuple[Tuple[str, str], ...], float] = {}

    def set(self, val: float, labels: Optional[Dict[str, str]] = None):
        key = tuple(sorted(labels.items())) if labels else ()
        with self._lock:
            self._values[key] = float(val)

    def inc(self, amount: float = 1.0, labels: Optional[Dict[str, str]] = None):
        key = tuple(sorted(labels.items())) if labels else ()
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + amount

    def dec(self, amount: float = 1.0, labels: Optional[Dict[str, str]] = None):
        key = tuple(sorted(labels.items())) if labels else ()
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) - amount

    def get(self, labels: Optional[Dict[str, str]] = None) -> float:
        key = tuple(sorted(labels.items())) if labels else ()
        with self._lock:
            return self._values.get(key, 0.0)

    def render(self) -> List[str]:
        lines = [
            f"# HELP {self.name} {self.description}",
            f"# TYPE {self.name} gauge",
        ]
        with self._lock:
            if not self._values:
                lines.append(f"{self.name} 0.0")
            else:
                for key, val in sorted(self._values.items()):
                    lbl_str = self._format_labels(dict(key))
                    lines.append(f"{self.name}{lbl_str} {val}")
        return lines


class Histogram(Metric):
    """Histogram samples observations (typically durations or sizes) into configurable buckets."""

    DEFAULT_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)

    def __init__(self, name: str, description: str, buckets: Optional[Tuple[float, ...]] = None):
        super().__init__(name, description, "histogram")
        self.buckets = sorted(buckets or self.DEFAULT_BUCKETS)
        self._sums: Dict[Tuple[Tuple[str, str], ...], float] = {}
        self._counts: Dict[Tuple[Tuple[str, str], ...], int] = {}
        self._bucket_counts: Dict[Tuple[Tuple[str, str], ...], Dict[float, int]] = {}

    def observe(self, amount: float, labels: Optional[Dict[str, str]] = None):
        key = tuple(sorted(labels.items())) if labels else ()
        with self._lock:
            self._sums[key] = self._sums.get(key, 0.0) + amount
            self._counts[key] = self._counts.get(key, 0) + 1
            if key not in self._bucket_counts:
                self._bucket_counts[key] = {b: 0 for b in self.buckets}
            for b in self.buckets:
                if amount <= b:
                    self._bucket_counts[key][b] += 1

    def time(self, labels: Optional[Dict[str, str]] = None):
        """Context manager to measure and observe duration."""
        return _HistogramTimer(self, labels)

    def render(self) -> List[str]:
        lines = [
            f"# HELP {self.name} {self.description}",
            f"# TYPE {self.name} histogram",
        ]
        with self._lock:
            if not self._counts:
                lines.append(f"{self.name}_count 0")
                lines.append(f"{self.name}_sum 0.0")
            else:
                for key in sorted(self._counts.keys()):
                    lbls = dict(key)
                    # Buckets
                    b_counts = self._bucket_counts[key]
                    for b in self.buckets:
                        b_lbls = {**lbls, "le": str(b)}
                        lines.append(f"{self.name}_bucket{self._format_labels(b_lbls)} {b_counts[b]}")
                    inf_lbls = {**lbls, "le": "+Inf"}
                    lines.append(f"{self.name}_bucket{self._format_labels(inf_lbls)} {self._counts[key]}")
                    lines.append(f"{self.name}_count{self._format_labels(lbls)} {self._counts[key]}")
                    lines.append(f"{self.name}_sum{self._format_labels(lbls)} {self._sums[key]}")
        return lines


class _HistogramTimer:
    def __init__(self, histogram: Histogram, labels: Optional[Dict[str, str]]):
        self.histogram = histogram
        self.labels = labels
        self.start = 0.0

    def __enter__(self):
        self.start = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        duration = time.perf_counter() - self.start
        self.histogram.observe(duration, self.labels)


class MetricsRegistry:
    """Singleton registry holding all registered metrics for Prometheus exposition."""

    def __init__(self):
        self._metrics: Dict[str, Metric] = {}
        self._lock = threading.Lock()

    def register(self, metric: Metric):
        with self._lock:
            self._metrics[metric.name] = metric
            return metric

    def counter(self, name: str, description: str) -> Counter:
        return self.register(Counter(name, description))

    def gauge(self, name: str, description: str) -> Gauge:
        return self.register(Gauge(name, description))

    def histogram(self, name: str, description: str, buckets: Optional[Tuple[float, ...]] = None) -> Histogram:
        return self.register(Histogram(name, description, buckets))

    def generate_prometheus_text(self) -> str:
        """Returns standard OpenMetrics / Prometheus plaintext exposition format."""
        all_lines = []
        with self._lock:
            for metric in self._metrics.values():
                all_lines.extend(metric.render())
        return "\n".join(all_lines) + "\n"

    generate_metrics_text = generate_prometheus_text


# Global registry
REGISTRY = MetricsRegistry()

# -------------------------------------------------------------------------
# Architecture Section 41 Standard Metric Definitions
# -------------------------------------------------------------------------
COLLECTOR_POLLS_TOTAL = REGISTRY.counter(
    "collector_polls_total",
    "Total number of polling cycles executed by Fonbet collector"
)
COLLECTOR_ERRORS_TOTAL = REGISTRY.counter(
    "collector_errors_total",
    "Total number of collector network and parsing errors"
)
COLLECTOR_EVENTS_SEEN = REGISTRY.gauge(
    "collector_events_seen",
    "Current count of active events observed in the latest polling snapshot"
)
COLLECTOR_PARSE_LATENCY = REGISTRY.histogram(
    "collector_parse_latency_seconds",
    "Latency of parsing raw Fonbet response payload into structured models"
)
COLLECTOR_PARSE_LATENCY_SECONDS = COLLECTOR_PARSE_LATENCY

ODDS_UPDATES_TOTAL = REGISTRY.counter(
    "odds_updates_total",
    "Total number of odds and market updates ingested"
)

ML_PREDICTIONS_TOTAL = REGISTRY.counter(
    "ml_predictions_total",
    "Total number of ML predictions generated by neural models"
)
ML_EDGE_VALUE = REGISTRY.gauge(
    "ml_edge_value",
    "Latest computed edge (positive advantage) for evaluated candidate"
)

RESEARCH_REQUESTS_TOTAL = REGISTRY.counter(
    "research_requests_total",
    "Total number of Web Research requests processed"
)
RESEARCH_CACHE_HITS_TOTAL = REGISTRY.counter(
    "research_cache_hits_total",
    "Total number of Web Research cache hits without network fetches"
)
RESEARCH_DURATION_SECONDS = REGISTRY.histogram(
    "research_duration_seconds",
    "Duration of web research and browser text extraction"
)

LLM_REQUESTS_TOTAL = REGISTRY.counter(
    "llm_requests_total",
    "Total number of qualitative requests dispatched to local LLM"
)
LLM_VERDICT_COUNTS = REGISTRY.counter(
    "llm_verdict_counts",
    "Distribution of LLM structured verdicts (BET, NO_BET, INSUFFICIENT_DATA)"
)
LLM_LATENCY_SECONDS = REGISTRY.histogram(
    "llm_latency_seconds",
    "Inference latency of local LLM responses"
)

BET_PROPOSALS_TOTAL = REGISTRY.counter(
    "bet_proposals_total",
    "Total number of Bet Proposals generated by the decision layer"
)
BET_REJECTED_TOTAL = REGISTRY.counter(
    "bet_rejected_total",
    "Total number of proposals rejected by bet-manager risk controls"
)
BET_EXECUTED_TOTAL = REGISTRY.counter(
    "bet_executed_total",
    "Total number of paper bets successfully placed and committed to bankroll"
)

SETTLEMENTS_TOTAL = REGISTRY.counter(
    "settlements_total",
    "Total count of bet settlements processed (WON, LOST, VOID)"
)
BANKROLL_BALANCE = REGISTRY.gauge(
    "virtual_bankroll_balance_rub",
    "Current virtual bankroll available balance in RUB"
)
VIRTUAL_BANKROLL_BALANCE_RUB = BANKROLL_BALANCE

BANKROLL_EXPOSURE = REGISTRY.gauge(
    "virtual_bankroll_exposure_rub",
    "Current total locked exposure of open pending bets in RUB"
)
VIRTUAL_BANKROLL_EXPOSURE_RUB = BANKROLL_EXPOSURE
