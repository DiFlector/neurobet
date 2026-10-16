"""python -m app.compare --sport tennis --phase prematch|live|all"""

import argparse
import json
import logging

from db.connection import SessionLocal

from ..dataset import get_dataset_builder
from .feature_run import run_feature_holdout
from .load import alias_map, load_external_prematch, load_fonbet, load_history
from .run import run_ablation, run_comparison, run_hypothesis

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("neural.compare")


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare tennis probability models on a frozen future period")
    parser.add_argument("--sport", default="tennis")
    parser.add_argument("--phase", default="all", choices=["prematch", "live", "all"])
    parser.add_argument("--ablations-only", action="store_true", help="Score market, Elo, score and full residual on the frozen test")
    parser.add_argument("--test-matches", type=int, default=0, help="Score one finalist per family on the latest N matches")
    parser.add_argument("--production-features", action="store_true", help="Score candidates on the production 103-feature tape")
    args = parser.parse_args()
    if args.sport != "tennis":
        raise SystemExit("Стенд сравнения сейчас собран для тенниса.")
    with SessionLocal() as session:
        if args.production_features:
            matches, names = get_dataset_builder(args.sport).load_matches(session)
            payload = run_feature_holdout(session, matches, names, test_matches=args.test_matches or 100)
            summary = [
                {
                    "family": item.get("family"),
                    "log_loss": (item.get("metrics") or {}).get("log_loss"),
                    "brier": (item.get("metrics") or {}).get("brier_score"),
                    "accuracy": (item.get("metrics") or {}).get("accuracy"),
                    "auc": (item.get("metrics") or {}).get("roc_auc"),
                    "ece": (item.get("metrics") or {}).get("expected_calibration_error"),
                    "versus_logistic": item.get("versus_logistic"),
                    "versus_boosting": item.get("versus_boosting"),
                    "decisions": item.get("decisions"),
                    "by_market_family": item.get("by_market_family"),
                }
                for item in payload["candidates"]
            ]
            logger.info("feature103\n%s", json.dumps(summary, ensure_ascii=False, indent=2))
            return
        aliases = alias_map(session)
        history = load_history(session, aliases)
        logger.info("History matches and serve logs: %d", len(history))
        jobs = []
        if args.phase in ("prematch", "all"):
            jobs.append(("prematch", load_fonbet(session, "prematch", aliases)))
            jobs.append(("external_prematch", load_external_prematch(session)))
        if args.phase in ("live", "all"):
            jobs.append(("live", load_fonbet(session, "live", aliases)))
        for name, rows in jobs:
            logger.info("Phase %s rows=%d matches=%d", name, len(rows), len({row.event_id for row in rows}))
            if len({row.event_id for row in rows}) < 6:
                logger.info("Skip %s: not enough matches.", name)
                continue
            if args.test_matches:
                payload = run_hypothesis(session, rows, history, name, test_matches=args.test_matches)
            else:
                runner = run_ablation if args.ablations_only else run_comparison
                payload = runner(session, rows, history, name)
            summary = [
                {
                    "family": item.get("family"),
                    "name": item["name"],
                    "calibrator": item.get("calibrator"),
                    "log_loss": item["metrics"]["log_loss"],
                    "brier": item["metrics"]["brier_score"],
                    "ece": item["metrics"]["expected_calibration_error"],
                    "auc": item["metrics"]["roc_auc"],
                    "roi": item["bets"]["roi"],
                    "bets": item["bets"]["bets"],
                    "beats_market": item.get("beats_market"),
                    "versus_market": item.get("log_loss_minus_market"),
                }
                for item in payload["candidates"]
            ]
            logger.info("%s\n%s", name, json.dumps(summary, ensure_ascii=False, indent=2))
            logger.info("tour slices %s", json.dumps(payload.get("tour_slices") or [], ensure_ascii=False))


if __name__ == "__main__":
    main()
