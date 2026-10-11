"""Load and throughput benchmark for Collector parsing pipeline."""

import time
import sys
import uuid

# Ensure package paths are present
paths = [
    "/srv/neurobet/packages/contracts/src",
    "/srv/neurobet/packages/features/src",
    "/srv/neurobet/packages/db/src",
    "/srv/neurobet/packages/sports-core/src",
    "/srv/neurobet/sports/tennis/src",
    "/srv/neurobet/sports/basketball/src",
    "/srv/neurobet/sports/volleyball/src",
    "/srv/neurobet/sports/hockey/src",
    "/srv/neurobet/sports/football/src",
    "/srv/neurobet/services/collector",
    "/app/packages/contracts/src",
    "/app/packages/features/src",
    "/app/packages/db/src",
    "/app/packages/sports-core/src",
    "/app/sports/tennis/src",
    "/app/sports/basketball/src",
    "/app/sports/volleyball/src",
    "/app/sports/hockey/src",
    "/app/sports/football/src",
    "/app",
]
for p in paths:
    if p not in sys.path:
        sys.path.insert(0, p)

from sports_core import registry
from tennis_adapter import TennisSportAdapter
from basketball_adapter import BasketballSportAdapter
from volleyball_adapter import VolleyballSportAdapter
from hockey_adapter import HockeySportAdapter
from football_adapter import FootballSportAdapter


def generate_mock_event_payload(idx: int, sport: str) -> dict:
    """Generates realistic synthetic bookmaker event with markets."""
    return {
        "id": f"evt_{sport}_{idx}",
        "source_event_id": f"fonbet_{sport}_{idx}",
        "sport_code": sport,
        "name": f"Team Alpha {idx} - Team Beta {idx}",
        "tournament": f"Championship {sport.capitalize()}",
        "is_live": True,
        "status": "live",
        "score": "2:1 (1:0, 1:1)" if sport in ("football", "hockey") else "105:98",
        "minute": 65,
        "markets": [
            {
                "market_type": "match_winner",
                "selections": [
                    {"name": "team_a", "outcome": "team_a", "odds": 1.85},
                    {"name": "team_b", "outcome": "team_b", "odds": 1.95},
                ],
            },
            {
                "market_type": "total_goals" if sport in ("football", "hockey") else "total_points",
                "param": 2.5 if sport in ("football", "hockey") else 215.5,
                "selections": [
                    {"name": "over", "outcome": "over", "odds": 1.90},
                    {"name": "under", "outcome": "under", "odds": 1.90},
                ],
            },
        ],
    }


def test_collector_parsing_load():
    """
    Simulates high-throughput line update bursts:
    500 multi-sport events with active markets processed concurrently.
    Measures processing time, throughput (events/second), and SLA (< 5ms per event).
    """
    print("\n--- Running Collector High-Throughput Load Test ---")
    sports = ["tennis", "basketball", "volleyball", "hockey", "football"]
    adapters = {
        "tennis": TennisSportAdapter(),
        "basketball": BasketballSportAdapter(),
        "volleyball": VolleyballSportAdapter(),
        "hockey": HockeySportAdapter(),
        "football": FootballSportAdapter(),
    }

    total_events = 500
    payloads = [
        generate_mock_event_payload(i, sports[i % len(sports)])
        for i in range(total_events)
    ]

    start_time = time.perf_counter()
    parsed_events = []
    parsed_states = []
    parsed_odds = []

    for item in payloads:
        sp = item["sport_code"]
        adapter = adapters[sp]
        evt = adapter.parse_event(item)
        state = adapter.parse_state(item)

        parse_odds_fn = getattr(adapter, "parse_odds_snapshot", None) or getattr(adapter, "parse_odds", None)
        odds = parse_odds_fn(item)
        parsed_events.append(evt)
        parsed_states.append(state)
        parsed_odds.append(odds)



    elapsed = time.perf_counter() - start_time
    throughput = total_events / elapsed
    avg_latency_ms = (elapsed / total_events) * 1000

    print(f"Processed: {total_events} events across 5 sports")
    print(f"Elapsed Time: {elapsed:.3f} s")
    print(f"Throughput: {throughput:.1f} events/sec")
    print(f"Average Latency: {avg_latency_ms:.3f} ms/event")

    # Assertions
    assert len(parsed_events) == total_events
    assert len(parsed_states) == total_events
    assert len(parsed_odds) == total_events
    assert throughput > 250.0, f"Throughput {throughput:.1f} events/sec below SLA (250 events/sec)"
    assert avg_latency_ms < 5.0, f"Average latency {avg_latency_ms:.3f} ms exceeds 5ms limit"

    print("✓ Collector load and throughput test passed successfully!")


if __name__ == "__main__":
    test_collector_parsing_load()
