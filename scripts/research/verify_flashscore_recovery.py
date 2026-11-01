"""Bounded live acceptance: one parked profile and one source-only PBP task.

--wait-cooldown waits for the CURRENT pause only. A renewed pause ends the run.
Never changes pause timestamps, queued jobs, model state or global feature flags.
"""
from __future__ import annotations
import argparse
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import time

from sqlalchemy import text
from db.connection import engine
from app.flashscore_recovery import diagnostics, enabled
from app.main import _flashscore_source_access, _flashscore_source_paused
from app.point_history import collect_source_point_history
from app.tennis_flashscore import FlashscoreTennisImporter, _profile_http_get


def run(output: Path, wait_cooldown=False):
    report = {"started_at": datetime.now(timezone.utc).isoformat(), "status": "pending", "verified": False}
    def save():
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str)+"\n")
    save()
    if not enabled():
        report["status"] = "recovery_disabled"
        save()
        return report
    pause = _flashscore_source_paused()
    report["initial_cooldown_until"] = pause.isoformat() if pause else None
    if pause and wait_cooldown:
        report["status"] = "waiting_for_existing_cooldown"
        save()
        while (remaining := (pause-datetime.now(timezone.utc)).total_seconds()) > 0:
            time.sleep(min(30, remaining))
    if _flashscore_source_paused():
        report["status"] = "deferred_source_cooldown"
        save()
        return report
    with engine.connect() as connection:
        profiles = connection.execute(text("""
            SELECT profile_urls FROM flashscore_import_jobs
            WHERE last_error='CAPTCHA_CHALLENGE_ABORTED' AND jsonb_array_length(profile_urls)=1
            ORDER BY created_at LIMIT 1
        """)).scalar()
        point_job = connection.execute(text("""
            SELECT source_match_id,source_url FROM tennis_point_source_refresh_jobs
            WHERE status IN ('queued','blocked','retry') ORDER BY requested_at LIMIT 1
        """)).mappings().first()
    if not profiles or not point_job:
        report["status"] = "acceptance_candidates_missing"
        save()
        return report
    report.update(profile_url=profiles[0], pbp_source_match_id=point_job["source_match_id"])
    report["status"] = "running"
    save()
    access = lambda: _flashscore_source_access("participant")
    try:
        result = FlashscoreTennisImporter().scrape_profile(
            profiles[0], stop_before_date=datetime.now(timezone.utc)-timedelta(days=30), source_access=access)
        report["profile"] = {k: v for k, v in asdict(result).items() if k != "profile"}
        report["profile"]["matches"] = len(result.profile.matches) if result.profile else 0
        save()
        if _flashscore_source_paused():
            report["status"] = "profile_blocked_pbp_deferred"
            return report
        with engine.connect() as connection:
            first_session = connection.execute(text("SELECT session_id FROM flashscore_browser_recovery WHERE id=1")).scalar()
        repeat_status, _, _, repeat_error = _profile_http_get(profiles[0], source_access=access)
        with engine.connect() as connection:
            second_session = connection.execute(text("SELECT session_id FROM flashscore_browser_recovery WHERE id=1")).scalar()
        report["session_reused"] = bool(first_session and first_session == second_session and
                                         repeat_status == 200 and not repeat_error and diagnostics().get("fetch_transport") == "flaresolverr")
        if _flashscore_source_paused():
            report["status"] = "repeat_blocked_pbp_deferred"
            return report
        report["pbp"] = collect_source_point_history(
            point_job["source_match_id"], point_job["source_url"], source_access=access)
        report["verified"] = bool(result.profile and result.profile.matches and result.archived_path and
                                   report["session_reused"] and report["pbp"].get("documents_saved", 0) > 0 and
                                   report["pbp"].get("confirmed_points", 0) > 0)
        report["status"] = "verified" if report["verified"] else "not_verified"
    except Exception as exc:
        report.update(status="failed", error_type=type(exc).__name__)
    finally:
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        with engine.connect() as connection:
            report["recovery"] = dict(connection.execute(text("""
                SELECT session_expires_at,last_result,lease_until FROM flashscore_browser_recovery WHERE id=1
            """)).mappings().one())
        save()
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--wait-cooldown", action="store_true")
    args = parser.parse_args()
    result = run(args.output, args.wait_cooldown)
    print(json.dumps({"status": result["status"], "verified": result["verified"]}))
