"""Benchmark parser revisions against the same archived Flashscore PBP HTML.

This is a read-only offline benchmark: source HTML is loaded before timing,
both parsers receive the exact same strings, and no Flashscore request or DB
write is performed. Pass a parser module file from the baseline revision.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from db.connection import SessionLocal
from db.models import TennisPointDocument, TennisPointMatchLink
from app.storage import ResearchStorage
from tennis_adapter.point_history import PARSER_VERSION, parse_point_history_html


def _load_parser(path: Path) -> tuple[str, Callable[[str], dict[str, Any]]]:
    spec = importlib.util.spec_from_file_location("baseline_point_history_parser", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot_load_baseline_module:{path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    parser = getattr(module, "parse_point_history_html", None)
    if not callable(parser):
        raise RuntimeError("baseline_parser_function_missing")
    return str(getattr(module, "PARSER_VERSION", "unknown")), parser


def _load_corpus(limit: int) -> list[dict[str, Any]]:
    storage = ResearchStorage()
    corpus: list[dict[str, Any]] = []
    seen: set[str] = set()
    with SessionLocal() as session:
        links = session.query(TennisPointMatchLink).filter(
            TennisPointMatchLink.status.in_(("matched", "manual")),
            TennisPointMatchLink.fonbet_event_id.is_not(None),
        ).order_by(TennisPointMatchLink.available_at.desc(), TennisPointMatchLink.id.desc()).all()
        for link in links:
            if link.source_match_id in seen:
                continue
            seen.add(link.source_match_id)
            document = session.query(TennisPointDocument).filter(
                TennisPointDocument.source_match_id == link.source_match_id,
                TennisPointDocument.archived_path.is_not(None),
            ).order_by(TennisPointDocument.retrieved_at.desc(), TennisPointDocument.id.desc()).first()
            if document is None:
                continue
            archived = storage.read_document(document.archived_path)
            html = archived.get("html") if isinstance(archived, dict) else None
            if not isinstance(html, str) or not html:
                continue
            corpus.append({
                "source_match_id": link.source_match_id,
                "fonbet_event_id": str(link.fonbet_event_id),
                "document_id": str(document.id),
                "content_hash": document.content_hash,
                "parser_version_stored": document.parser_version,
                "retrieved_at": document.retrieved_at.isoformat() if document.retrieved_at else None,
                "html": html,
            })
            if len(corpus) >= limit:
                break
    return corpus


def _signature(parsed: dict[str, Any]) -> list[tuple[Any, ...]]:
    fields = (
        "set", "game_score", "point_index", "state_before", "state_after",
        "point_score", "server", "winner", "tie_break", "confirmed", "quality",
    )
    return [tuple(json.dumps(event.get(key), sort_keys=True, ensure_ascii=False) for key in fields)
            for event in parsed.get("events", [])]


def _measure(parser: Callable[[str], dict[str, Any]], html_pages: list[str], iterations: int) -> dict[str, Any]:
    for html in html_pages[: min(3, len(html_pages))]:
        parser(html)
    durations: list[float] = []
    event_count = 0
    for _ in range(iterations):
        start = time.perf_counter()
        for html in html_pages:
            event_count += len(parser(html).get("events", []))
        durations.append(time.perf_counter() - start)
    median = statistics.median(durations)
    total_bytes = sum(len(html.encode("utf-8")) for html in html_pages)
    parse_count = len(html_pages) * iterations
    return {
        "iterations": iterations,
        "parse_count": parse_count,
        "median_seconds": median,
        "min_seconds": min(durations),
        "max_seconds": max(durations),
        "documents_per_second": parse_count / median if median else None,
        "megabytes_per_second": (total_bytes * iterations / 1_000_000) / median if median else None,
        "events_per_second": event_count / median if median else None,
        "events_per_parse": event_count / parse_count if parse_count else 0,
        "total_html_bytes": total_bytes,
    }


def build_report(baseline_path: Path, limit: int, iterations: int, baseline_revision: str | None) -> dict[str, Any]:
    baseline_version, baseline_parser = _load_parser(baseline_path)
    corpus = _load_corpus(limit)
    if not corpus:
        raise RuntimeError("no_archived_pbp_documents_available")
    html_pages = [item["html"] for item in corpus]

    # Alternate order between iterations to reduce warm-cache/order bias.
    baseline_runs, current_runs, parity = [], [], []
    for iteration in range(iterations):
        order = (("baseline", baseline_parser), ("current", parse_point_history_html))
        if iteration % 2:
            order = tuple(reversed(order))
        output: dict[str, list[dict[str, Any]]] = {}
        for label, parser in order:
            start = time.perf_counter()
            parsed = [parser(html) for html in html_pages]
            elapsed = time.perf_counter() - start
            output[label] = parsed
            (baseline_runs if label == "baseline" else current_runs).append(elapsed)
        for index, (before, after) in enumerate(zip(output["baseline"], output["current"])):
            parity.append({
                "source_match_id": corpus[index]["source_match_id"],
                "baseline_events": len(before.get("events", [])),
                "current_events": len(after.get("events", [])),
                "event_count_equal": len(before.get("events", [])) == len(after.get("events", [])),
                "exact_event_signature_equal": _signature(before) == _signature(after),
            })

    total_bytes = sum(len(html.encode("utf-8")) for html in html_pages)
    baseline_median = statistics.median(baseline_runs)
    current_median = statistics.median(current_runs)
    total_current_events = sum(len(parse_point_history_html(html).get("events", [])) for html in html_pages)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": "same archived HTML corpus; parse-only; warm-up excluded; alternating parser order",
        "network_used": False,
        "database_writes": 0,
        "corpus": {
            "documents": len(corpus),
            "unique_matches": len({item["source_match_id"] for item in corpus}),
            "total_html_bytes": total_bytes,
            "iterations_per_parser": iterations,
            "stored_parser_versions": sorted({item["parser_version_stored"] for item in corpus}),
        },
        "baseline": {
            "parser_version": baseline_version,
            "source_file": str(baseline_path),
            "revision": baseline_revision,
            "median_seconds": baseline_median,
            "min_seconds": min(baseline_runs),
            "max_seconds": max(baseline_runs),
            "documents_per_second": len(corpus) / baseline_median if baseline_median else None,
            "megabytes_per_second": total_bytes / 1_000_000 / baseline_median if baseline_median else None,
        },
        "current": {
            "parser_version": PARSER_VERSION,
            "median_seconds": current_median,
            "min_seconds": min(current_runs),
            "max_seconds": max(current_runs),
            "documents_per_second": len(corpus) / current_median if current_median else None,
            "megabytes_per_second": total_bytes / 1_000_000 / current_median if current_median else None,
            "events_per_corpus_pass": total_current_events,
        },
        "speedup_percent": ((baseline_median / current_median) - 1) * 100 if current_median else None,
        "semantic_comparison": {
            "document_comparisons": len(parity),
            "event_count_equal_documents": sum(item["event_count_equal"] for item in parity),
            "exact_event_signature_equal_documents": sum(item["exact_event_signature_equal"] for item in parity),
            "differences": [item for item in parity if not item["exact_event_signature_equal"]],
            "note": "A difference is not automatically a regression: v4 fixes known server-side and terminal-event semantics; inspect against the separate source DOM review.",
        },
        "documents": [{key: value for key, value in item.items() if key != "html"} for item in corpus],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-module", required=True, type=Path)
    parser.add_argument("--baseline-revision")
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--iterations", type=int, default=8)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = build_report(args.baseline_module, args.limit, args.iterations, args.baseline_revision)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    summary = {key: value for key, value in report.items() if key not in {"documents"}}
    summary["semantic_comparison"] = {key: value for key, value in report["semantic_comparison"].items() if key != "differences"}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
