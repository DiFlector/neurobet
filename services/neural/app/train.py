"""CLI entrypoint for running ML training runs across all supported sports."""

import argparse
import logging
import os
import sys

from db.connection import SessionLocal
from .trainer import ModelTrainer

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s UTC [%(levelname)s] [trainer] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("neural.trainer")

SUPPORTED_SPORTS = ["tennis", "football", "basketball", "hockey", "volleyball"]


def main():
    parser = argparse.ArgumentParser(description="Neurobet ML Model Training CLI")
    parser.add_argument(
        "--sport",
        default="tennis",
        help="Sport code (tennis, football, basketball, hockey, volleyball, or 'all')",
    )
    parser.add_argument(
        "--model",
        default="all",
        choices=["lr", "gb", "all"],
        help="Model type: lr, gb, or all",
    )
    parser.add_argument(
        "--checkpoint-dir",
        default="/app/models/checkpoints",
        help="Output directory for checkpoints",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for reproducibility",
    )
    parser.add_argument(
        "--bump",
        default="patch",
        choices=["patch", "minor", "major"],
        help="Version step: patch tennis_v1.0.0->tennis_v1.0.1, minor tennis_v1.0.0->tennis_v1.1.0, major tennis_v1.0.0->tennis_v2.0.0",
    )
    args = parser.parse_args()

    logger.info("Starting ML training run for sport='%s', model='%s', seed=%d", args.sport, args.model, args.seed)

    trainer = ModelTrainer(
        checkpoint_dir=args.checkpoint_dir,
        random_seed=args.seed,
    )

    with SessionLocal() as session:
        results = []
        if args.sport.lower() == "all":
            for sport in SUPPORTED_SPORTS:
                logger.info("--- Training models for sport: %s ---", sport)
                if args.model == "all":
                    res = trainer.train_all(session=session, sport_code=sport, bump=args.bump)
                else:
                    res = [trainer.train_model(session=session, model_type=args.model, sport_code=sport, bump=args.bump)]
                results.extend(res)
        else:
            if args.model == "all":
                results = trainer.train_all(session=session, sport_code=args.sport.lower(), bump=args.bump)
            else:
                results = [
                    trainer.train_model(
                        session=session,
                        model_type=args.model,
                        sport_code=args.sport.lower(),
                        bump=args.bump,
                    )
                ]

    logger.info("=== TRAINING RUN SUMMARY ===")
    for res in results:
        m = res["metrics_val"]
        ds = res.get("dataset_meta") or {}
        logger.info(
            "Dataset: matches=%s rows=%s train=%s val=%s",
            ds.get("match_count"),
            ds.get("row_count"),
            ds.get("train_samples"),
            ds.get("val_samples"),
        )
        logger.info(
            "Sport: %s | Model: %s | Version: %s | LogLoss: %.4f | Brier: %.4f | Acc: %.4f | AUC: %.4f | ECE: %.4f",
            res["model_name"].split("_")[0].upper(),
            res["model_name"],
            res["version_tag"],
            m.get("log_loss", 0.0),
            m.get("brier_score", 0.0),
            m.get("accuracy", 0.0),
            m.get("roc_auc", 0.0),
            m.get("expected_calibration_error", 0.0),
        )
        logger.info("Saved Checkpoint: %s", res["artifact_path"])
        logger.info("Registered in DB: ModelVersion ID=%s, TrainingRun ID=%s", res["model_version_id"], res["training_run_id"])

    logger.info("Total models trained and registered: %d", len(results))
    logger.info("Training completed successfully.")


if __name__ == "__main__":
    main()
