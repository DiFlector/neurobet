"""
Fonbet Machine Learning & Deep Learning Training CLI
Trains LightGBM + PyTorch models in Prematch mode to prevent live score bias leakage.
"""

import argparse
import json
import logging
import os
import sys
import time
import numpy as np
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score

from ml.dataset import OddsDatasetLoader
from ml.lightgbm_model import LightGBMPredictor
from ml.pytorch_model import PyTorchOddsPredictor
from ml.ensemble import EnsemblePredictor

sys.stdout.reconfigure(encoding='utf-8')
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s: %(message)s")
logger = logging.getLogger("train_models")

def main():
    parser = argparse.ArgumentParser(description="Train LightGBM + PyTorch Betting Models")
    parser.add_argument("--data", default="history_db/fonbet_history_2years.jsonl", help="Dataset file path")
    parser.add_argument("--mode", choices=["prematch", "live"], default="prematch", help="Training mode (default: prematch)")
    parser.add_argument("--max-samples", type=int, default=300000, help="Maximum samples to load (default: 300000)")
    parser.add_argument("--epochs", type=int, default=20, help="PyTorch training epochs (default: 20)")
    parser.add_argument("--models-dir", default="models", help="Directory to save model checkpoints")
    parser.add_argument("--out-dir", default="output", help="Directory for output reports")
    parser.add_argument("--min-ev", type=float, default=0.05, help="Minimum EV threshold (default: 0.05 = 5%%)")
    args = parser.parse_args()

    os.makedirs(args.models_dir, exist_ok=True)
    os.makedirs(args.out_dir, exist_ok=True)

    t_start = time.time()

    # Step 1: Load Dataset in requested mode
    logger.info(f"Loading dataset from {args.data} (mode={args.mode})...")
    loader = OddsDatasetLoader(data_file=args.data)
    X, y_1x2, y_over, samples, feature_names = loader.load_dataset(max_samples=args.max_samples, mode=args.mode)

    t_loaded = time.time()
    logger.info(f"Successfully loaded {len(X)} samples with {X.shape[1]} features in {t_loaded - t_start:.2f}s.")

    # Step 2: Split Train (80%) / Test (20%)
    if len(X) >= 10:
        X_train, X_test, y_1x2_train, y_1x2_test, y_over_train, y_over_test, samples_train, samples_test = train_test_split(
            X, y_1x2, y_over, samples, test_size=0.2, random_state=42
        )
    else:
        X_train, X_test = X, X
        y_1x2_train, y_1x2_test = y_1x2, y_1x2
        y_over_train, y_over_test = y_over, y_over
        samples_train, samples_test = samples, samples

    logger.info(f"Split dataset -> Train: {len(X_train)} samples | Test: {len(X_test)} samples.")

    # Step 3: Train LightGBM Model
    logger.info("⚡ [1/2] Training LightGBM Model...")
    t_lgb_0 = time.time()
    lgb_model = LightGBMPredictor()
    lgb_model.train(X_train, y_1x2_train, y_over_train)
    t_lgb_1 = time.time()

    lgb_p_1x2, lgb_p_over = lgb_model.predict_proba(X_test)
    lgb_acc = accuracy_score(y_1x2_test, np.argmax(lgb_p_1x2, axis=1))
    logger.info(f"  ✓ LightGBM training finished in {t_lgb_1 - t_lgb_0:.2f}s | Test Accuracy (1X2): {lgb_acc * 100:.2f}%")

    # Save LightGBM
    lgb_path = os.path.join(args.models_dir, "lightgbm_model.pkl")
    lgb_model.save(lgb_path)

    # Step 4: Train PyTorch Neural Network
    logger.info(f"🧠 [2/2] Training PyTorch OddsNet Neural Network ({args.epochs} epochs)...")
    t_pt_0 = time.time()
    pt_model = PyTorchOddsPredictor(input_dim=X.shape[1])
    pt_model.train(X_train, y_1x2_train, y_over_train, epochs=args.epochs, batch_size=256)
    t_pt_1 = time.time()

    pt_p_1x2, pt_p_over = pt_model.predict_proba(X_test)
    pt_acc = accuracy_score(y_1x2_test, np.argmax(pt_p_1x2, axis=1))
    logger.info(f"  ✓ PyTorch training finished in {t_pt_1 - t_pt_0:.2f}s | Test Accuracy (1X2): {pt_acc * 100:.2f}%")

    # Save PyTorch
    pt_path = os.path.join(args.models_dir, "pytorch_model.pt")
    pt_model.save(pt_path)

    # Step 5: Ensemble Prediction
    logger.info("🤝 Blending predictions in Ensemble Predictor (50% LightGBM + 50% PyTorch)...")
    ensemble = EnsemblePredictor(lgb_model, pt_model, weight_lgb=0.5)
    ens_p_1x2, ens_p_over = ensemble.predict_proba(X_test)
    ens_acc = accuracy_score(y_1x2_test, np.argmax(ens_p_1x2, axis=1))
    logger.info(f"  ✓ Ensemble Test Accuracy (1X2): {ens_acc * 100:.2f}%")

    t_end = time.time()
    logger.info(f"=====================================================")
    logger.info(f" 🎉 TRAINING COMPLETE IN {t_end - t_start:.2f} SECONDS")
    logger.info(f"    • Models saved: {lgb_path}, {pt_path}")
    logger.info(f"=====================================================")

if __name__ == "__main__":
    main()
