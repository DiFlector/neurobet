"""Audit rules, severity levels, and report schemas for data quality engine."""

import enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class IssueType(str, enum.Enum):
    DUPLICATE_EVENT = "DUPLICATE_EVENT"
    TIME_ORDER_REGRESSION = "TIME_ORDER_REGRESSION"
    INVALID_ODDS = "INVALID_ODDS"
    IMPOSSIBLE_SCORE_TRANSITION = "IMPOSSIBLE_SCORE_TRANSITION"
    CLOCK_REGRESSION = "CLOCK_REGRESSION"
    OBSERVATION_GAP = "OBSERVATION_GAP"
    ORPHAN_ODDS = "ORPHAN_ODDS"
    MISSING_RESULT = "MISSING_RESULT"


class Severity(str, enum.Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


# Penalty deductions per issue severity
SEVERITY_PENALTIES: Dict[Severity, float] = {
    Severity.INFO: 2.0,
    Severity.WARNING: 10.0,
    Severity.CRITICAL: 25.0,
}


class QualityIssueItem(BaseModel):
    issue_type: IssueType
    severity: Severity
    message: str
    details: Dict[str, Any] = Field(default_factory=dict)
    event_id: Optional[str] = None
    source_event_id: Optional[str] = None


class EventQualitySummary(BaseModel):
    event_id: str
    source_event_id: str
    sport_code: str
    quality_score: float  # 0.0 to 100.0
    issues_count: int
    critical_count: int
    warning_count: int
    info_count: int
    issues: List[QualityIssueItem] = Field(default_factory=list)


class DataQualityReport(BaseModel):
    total_events_checked: int
    total_issues_found: int
    average_quality_score: float
    healthy_events_count: int  # quality_score >= 80.0
    degraded_events_count: int  # 50.0 <= quality_score < 80.0
    corrupt_events_count: int   # quality_score < 50.0
    issues_by_type: Dict[str, int] = Field(default_factory=dict)
    issues_by_severity: Dict[str, int] = Field(default_factory=dict)
    audited_at: str
