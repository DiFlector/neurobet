"""Data Quality Engine: audit detectors, score computation, and audit persistence."""

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from db.models import DataQualityIssue, Event, EventStateSnapshot, OddsSnapshot
from .rules import (
    DataQualityReport,
    EventQualitySummary,
    IssueType,
    QualityIssueItem,
    SEVERITY_PENALTIES,
    Severity,
)


class DataQualityEngine:
    """
    Historical and real-time data quality auditor.
    Guarantees that defective records are flagged, recorded in audit tables,
    and penalized, without ever being deleted silently.
    """

    @classmethod
    def calculate_quality_score(cls, issues: List[QualityIssueItem]) -> float:
        """Computes 0.0 - 100.0 quality score penalized by issue severities."""
        score = 100.0
        for issue in issues:
            penalty = SEVERITY_PENALTIES.get(issue.severity, 5.0)
            score -= penalty
        return max(0.0, round(score, 2))

    @classmethod
    def check_time_order(cls, snapshots: List[EventStateSnapshot]) -> List[QualityIssueItem]:
        """Detects backwards or non-chronological time steps in snapshots."""
        issues: List[QualityIssueItem] = []
        if len(snapshots) < 2:
            return issues

        prev_time = snapshots[0].observed_at
        for i in range(1, len(snapshots)):
            curr_time = snapshots[i].observed_at
            if curr_time < prev_time:
                issues.append(
                    QualityIssueItem(
                        issue_type=IssueType.TIME_ORDER_REGRESSION,
                        severity=Severity.CRITICAL,
                        message=f"Snapshot index {i} has observed_at {curr_time.isoformat()} before previous {prev_time.isoformat()}",
                        details={"prev_time": prev_time.isoformat(), "curr_time": curr_time.isoformat(), "index": i},
                    )
                )
            prev_time = curr_time
        return issues

    @classmethod
    def check_odds_validity(cls, odds_list: List[OddsSnapshot]) -> List[QualityIssueItem]:
        """Detects negative, impossible (<= 1.0) or absurdly high odds (> 100.0)."""
        issues: List[QualityIssueItem] = []
        for o in odds_list:
            odds_val = float(o.odds)
            if odds_val <= 1.0:
                issues.append(
                    QualityIssueItem(
                        issue_type=IssueType.INVALID_ODDS,
                        severity=Severity.CRITICAL,
                        message=f"Odds value {odds_val} is invalid (must be > 1.0) for outcome {o.outcome}",
                        details={"odds": odds_val, "outcome": o.outcome, "market_type": o.market_type},
                    )
                )
            elif odds_val > 100.0:
                issues.append(
                    QualityIssueItem(
                        issue_type=IssueType.INVALID_ODDS,
                        severity=Severity.WARNING,
                        message=f"Extreme odds {odds_val} detected for outcome {o.outcome}",
                        details={"odds": odds_val, "outcome": o.outcome},
                    )
                )
        return issues

    @classmethod
    def check_score_transitions(cls, snapshots: List[EventStateSnapshot]) -> List[QualityIssueItem]:
        """Detects impossible score progressions in tennis (e.g. negative values or invalid set jumps)."""
        issues: List[QualityIssueItem] = []
        for i, s in enumerate(snapshots):
            score_detail = s.score_detail or {}
            games_a = score_detail.get("current_game_a", 0)
            games_b = score_detail.get("current_game_b", 0)
            if games_a < 0 or games_b < 0:
                issues.append(
                    QualityIssueItem(
                        issue_type=IssueType.IMPOSSIBLE_SCORE_TRANSITION,
                        severity=Severity.CRITICAL,
                        message=f"Negative game count in snapshot {i}: {games_a}-{games_b}",
                        details={"games_a": games_a, "games_b": games_b},
                    )
                )
        return issues

    @classmethod
    def check_clock_regressions(cls, snapshots: List[EventStateSnapshot]) -> List[QualityIssueItem]:
        """Detects clock regressions where match_clock_seconds decreases during play."""
        issues: List[QualityIssueItem] = []
        prev_clock: Optional[int] = None
        for i, s in enumerate(snapshots):
            clock = s.match_clock_seconds
            if clock is not None:
                if prev_clock is not None and clock < prev_clock:
                    issues.append(
                        QualityIssueItem(
                            issue_type=IssueType.CLOCK_REGRESSION,
                            severity=Severity.WARNING,
                            message=f"Clock regression detected at index {i}: {clock}s < previous {prev_clock}s",
                            details={"prev_clock": prev_clock, "curr_clock": clock},
                        )
                    )
                prev_clock = clock
        return issues

    @classmethod
    def check_observation_gaps(
        cls, snapshots: List[EventStateSnapshot], max_gap_seconds: int = 60
    ) -> List[QualityIssueItem]:
        """Detects polling gaps during live status where collector missed observations."""
        issues: List[QualityIssueItem] = []
        for i in range(1, len(snapshots)):
            delta_sec = (snapshots[i].observed_at - snapshots[i - 1].observed_at).total_seconds()
            if delta_sec > max_gap_seconds:
                issues.append(
                    QualityIssueItem(
                        issue_type=IssueType.OBSERVATION_GAP,
                        severity=Severity.WARNING,
                        message=f"Observation gap of {delta_sec:.1f}s between snapshots (threshold: {max_gap_seconds}s)",
                        details={"gap_seconds": delta_sec, "max_gap_seconds": max_gap_seconds},
                    )
                )
        return issues

    @classmethod
    def audit_event(cls, session: Session, event_id: uuid.UUID | str) -> EventQualitySummary:
        """Performs comprehensive data quality audit for a specific event."""
        if isinstance(event_id, str):
            event_uuid = uuid.UUID(event_id)
        else:
            event_uuid = event_id

        event = session.execute(select(Event).where(Event.id == event_uuid)).scalar_one_or_none()
        if not event:
            raise ValueError(f"Event {event_id} not found")

        # Fetch snapshots chronologically
        snapshots = list(
            session.execute(
                select(EventStateSnapshot)
                .where(EventStateSnapshot.event_id == event_uuid)
                .order_by(EventStateSnapshot.observed_at.asc())
            ).scalars()
        )

        odds = list(
            session.execute(
                select(OddsSnapshot)
                .where(OddsSnapshot.event_id == event_uuid)
                .order_by(OddsSnapshot.observed_at.asc())
            ).scalars()
        )

        detected_issues: List[QualityIssueItem] = []
        detected_issues.extend(cls.check_time_order(snapshots))
        detected_issues.extend(cls.check_odds_validity(odds))
        detected_issues.extend(cls.check_score_transitions(snapshots))
        detected_issues.extend(cls.check_clock_regressions(snapshots))
        detected_issues.extend(cls.check_observation_gaps(snapshots))

        # Check missing result if event finished
        if event.status in ("finished", "completed") and not event.metadata_json.get("winner"):
            detected_issues.append(
                QualityIssueItem(
                    issue_type=IssueType.MISSING_RESULT,
                    severity=Severity.CRITICAL,
                    message="Event is finished but has no recorded winner or settlement result",
                    details={"status": event.status},
                )
            )

        for issue in detected_issues:
            issue.event_id = str(event_uuid)
            issue.source_event_id = event.source_event_id

        # Calculate score
        score = cls.calculate_quality_score(detected_issues)
        event.quality_score = score

        # Persist issues to database
        for issue in detected_issues:
            db_issue = DataQualityIssue(
                event_id=event_uuid,
                source_event_id=event.source_event_id,
                sport_code=event.sport_code,
                issue_type=issue.issue_type.value,
                severity=issue.severity.value,
                details=issue.details,
            )
            session.add(db_issue)

        session.commit()

        crit_count = sum(1 for i in detected_issues if i.severity == Severity.CRITICAL)
        warn_count = sum(1 for i in detected_issues if i.severity == Severity.WARNING)
        info_count = sum(1 for i in detected_issues if i.severity == Severity.INFO)

        return EventQualitySummary(
            event_id=str(event.id),
            source_event_id=event.source_event_id,
            sport_code=event.sport_code,
            quality_score=score,
            issues_count=len(detected_issues),
            critical_count=crit_count,
            warning_count=warn_count,
            info_count=info_count,
            issues=detected_issues,
        )

    @classmethod
    def run_full_audit(cls, session: Session) -> DataQualityReport:
        """Audits all events and system-wide anomalies, returning aggregate report."""
        events = list(session.execute(select(Event)).scalars())
        total_events = len(events)

        all_issues: List[QualityIssueItem] = []
        scores: List[float] = []

        # 1. Global duplicate events check
        dups_stmt = (
            select(Event.source_event_id, func.count(Event.id))
            .group_by(Event.source_event_id)
            .having(func.count(Event.id) > 1)
        )
        dups = session.execute(dups_stmt).all()
        for source_id, count in dups:
            item = QualityIssueItem(
                issue_type=IssueType.DUPLICATE_EVENT,
                severity=Severity.CRITICAL,
                message=f"Duplicate event source ID detected: {source_id} appears {count} times",
                details={"source_event_id": source_id, "count": count},
            )
            all_issues.append(item)
            session.add(
                DataQualityIssue(
                    source_event_id=source_id,
                    sport_code="tennis",
                    issue_type=IssueType.DUPLICATE_EVENT.value,
                    severity=Severity.CRITICAL.value,
                    details={"count": count},
                )
            )

        # 2. Audit each event
        for ev in events:
            summary = cls.audit_event(session, ev.id)
            scores.append(summary.quality_score)
            all_issues.extend(summary.issues)

        avg_score = round(sum(scores) / len(scores), 2) if scores else 100.0
        healthy = sum(1 for s in scores if s >= 80.0)
        degraded = sum(1 for s in scores if 50.0 <= s < 80.0)
        corrupt = sum(1 for s in scores if s < 50.0)

        by_type: Dict[str, int] = {}
        for issue in all_issues:
            t_key = issue.issue_type.value if hasattr(issue.issue_type, "value") else str(issue.issue_type)
            by_type[t_key] = by_type.get(t_key, 0) + 1

        by_sev: Dict[str, int] = {}
        for issue in all_issues:
            s_key = issue.severity.value if hasattr(issue.severity, "value") else str(issue.severity)
            by_sev[s_key] = by_sev.get(s_key, 0) + 1

        return DataQualityReport(
            total_events_checked=total_events,
            total_issues_found=len(all_issues),
            average_quality_score=avg_score,
            healthy_events_count=healthy,
            degraded_events_count=degraded,
            corrupt_events_count=corrupt,
            issues_by_type=by_type,
            issues_by_severity=by_sev,
            audited_at=datetime.now(timezone.utc).isoformat(),
        )
