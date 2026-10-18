"""Train the three tennis_v3.0.0 lines and compare them on the same held-out matches."""

from __future__ import annotations

import argparse
import json
import logging
import os
from typing import Any, Dict, List

from .trainer import ModelTrainer

logger = logging.getLogger("neural.epoch_search")

FAMILIES = ("epoch_lr", "epoch_gb", "epoch_mlp")
VERSION_TAG = "tennis_v3.0.0"


def train_lines(checkpoint_dir: str) -> List[Dict[str, Any]]:
    from db.connection import SessionLocal

    trainer = ModelTrainer(checkpoint_dir=checkpoint_dir, random_seed=42)
    results = []
    with SessionLocal() as session:
        for model_type in FAMILIES:
            logger.info("Starting %s %s", model_type, VERSION_TAG)
            result = trainer.train_model(
                session=session,
                model_type=model_type,
                sport_code="tennis",
                market="match_winner",
                bump="major",
                version_tag=VERSION_TAG,
            )
            results.append(result)
            logger.info("Finished %s: %s", model_type, result.get("version_tag") or result.get("reason"))
    return results


def score_holdout(checkpoint_dir: str, results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Decision metrics on the 100 matches that were excluded from training."""
    import joblib
    from db.connection import SessionLocal

    from .dataset import decision_records, get_dataset_builder, thin_match
    from .epoch_fit import split_holdout
    from .metrics import compute_classification_metrics, insufficient_metrics

    report = {"version_tag": VERSION_TAG, "lines": []}
    with SessionLocal() as session:
        matches, names = get_dataset_builder("tennis").load_matches(session)
    _trainable, holdout = split_holdout(matches)
    report["holdout_event_ids"] = [str(match.event_id) for match in holdout]
    for result in results:
        path = result.get("artifact_path")
        if not path:
            continue
        model = joblib.load(path)["model"]
        records = decision_records(model, [thin_match(match) for match in holdout], names, "tennis")
        labels = [int(row["label"]) for row in records]
        probs = [float(row["probability"]) for row in records]
        if labels and len(set(labels)) > 1:
            metrics = compute_classification_metrics(labels, probs)
        else:
            metrics = insufficient_metrics()
        report["lines"].append(
            {
                "model_name": result.get("model_name"),
                "version_tag": result.get("version_tag"),
                "model_version_id": result.get("model_version_id"),
                "artifact_path": path,
                "decision_rows": len(labels),
                "log_loss": metrics.get("log_loss"),
                "brier_score": metrics.get("brier_score"),
                "accuracy": metrics.get("accuracy"),
                "roc_auc": metrics.get("roc_auc"),
            }
        )
    ranked = [row for row in report["lines"] if row.get("log_loss") is not None]
    ranked.sort(key=lambda row: (float(row["log_loss"]), float(row.get("brier_score") or 1.0)))
    report["winner"] = ranked[0] if ranked else None
    destination = os.path.join(checkpoint_dir, "epoch_v3_report.json")
    with open(destination, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2)
    logger.info("Holdout comparison written to %s", destination)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Train tennis_v3.0.0 epoch lines")
    parser.add_argument("--checkpoint-dir", default="/app/models/checkpoints")
    parser.add_argument("--family", choices=["lr", "gb", "mlp", "all"], default="all")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    selected = FAMILIES if args.family == "all" else (f"epoch_{args.family}",)
    from db.connection import SessionLocal

    trainer = ModelTrainer(checkpoint_dir=args.checkpoint_dir, random_seed=42)
    results = []
    with SessionLocal() as session:
        for model_type in selected:
            logger.info("Starting %s %s", model_type, VERSION_TAG)
            results.append(
                trainer.train_model(
                    session=session,
                    model_type=model_type,
                    sport_code="tennis",
                    market="match_winner",
                    bump="major",
                    version_tag=VERSION_TAG,
                )
            )
    if args.family == "all":
        score_holdout(args.checkpoint_dir, results)


if __name__ == "__main__":
    main()
