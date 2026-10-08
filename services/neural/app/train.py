"""CLI entrypoint for running ML training runs with a single command."""

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


def main():
    parser = argparse.ArgumentParser(description="Neurobet ML Model Training CLI")
    parser.add_argument("--sport", default="tennis", help="Sport code (e.g. tennis)")
    parser.add_argument("--model", default="all", choices=["lr", "gb", "all"], help="Model type: lr, gb, or all")
    parser.add_argument("--checkpoint-dir", default="/app/models/checkpoints", help="Output directory for checkpoints")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for reproducibility")
    args = parser.parse_args()

    logger.info("Starting ML training run for sport='%s', model='%s', seed=%d", args.sport, args.model, args.seed)

    trainer = ModelTrainer(
        checkpoint_dir=args.checkpoint_dir,
        random_seed=args.seed,
    )

    with SessionLocal() as session:
        if args.model == "all":
            results = trainer.train_all(session=session, sport_code=args.sport)
        else:
            results = [trainer.train_model(session=session, model_type=args.model, sport_code=args.sport)]

    logger.info("=== TRAINING RUN SUMMARY ===")
    for res in results:
        m = res["metrics_val"]
        logger.info(
            "Model: %s | Version: %s | LogLoss: %.4f | Brier: %.4f | Acc: %.4f | AUC: %.4f | ECE: %.4f",
            res["model_name"],
            res["version_tag"],
            m["log_loss"],
            m["brier_score"],
            m["accuracy"],
            m["roc_auc"],
            m["expected_calibration_error"],
        )
        logger.info("Saved Checkpoint: %s", res["artifact_path"])
        logger.info("Registered in DB: ModelVersion ID=%s, TrainingRun ID=%s", res["model_version_id"], res["training_run_id"])

    logger.info("Training completed successfully.")


if __name__ == "__main__":
    main()
