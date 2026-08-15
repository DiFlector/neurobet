import copy
import math
import random
import os
import logging
from typing import List, Dict, Any, Optional, Tuple

import torch
import torch.nn as nn
import torch.optim as optim
import numpy as np
import lightgbm as lgb

from app.config import MODEL_DIR
from app.neuralbet import bankroll
from app.neuralbet.context import (
    NUM_SPORTS, NUM_MARKET_FAMILIES, sport_index, market_family_index,
    TEAM_HASH_BUCKETS, team_index,
)

logger = logging.getLogger("ai_service_model")

PYTORCH_WEIGHTS_PATH = os.path.join(MODEL_DIR, "pytorch_gru.pt")
LIGHTGBM_MODEL_PATH = os.path.join(MODEL_DIR, "lightgbm_model.txt")

LGB_FEATURE_NAMES = ["coefficient", "initial_coefficient", "drop_ratio", "volatility", "samples_count", "factor_id", "score_diff", "sport_idx", "team1_idx", "team2_idx"]

SEQ_LEN = 10

# Hardware has headroom, so the sequence encoder is sized generously: two GRU layers,
# 64 hidden units. (Was 1 layer / 32 units when the only signal it saw was the odds
# curve — now that score_diff is a real per-step feature (see _build_sequence) instead
# of a broadcast constant, the extra capacity is there to actually use it.)
HIDDEN_DIM = int(os.getenv("NEURALBET_HIDDEN_DIM", "64"))
GRU_LAYERS = int(os.getenv("NEURALBET_GRU_LAYERS", "2"))

# Online-training epoch budget. With mini-batches + a held-out validation split (see
# pipeline.py), more epochs are just "try harder," not "memorize harder" — early
# stopping (best-val-epoch selection below) keeps it honest. Ceiling raised 60 -> 200
# alongside TRAIN_BATCH_TOTAL (pipeline.py) — early stopping already governs how many
# actually run (observed landing around 15-25 with the old 300-sample batches), this
# just removes an artificial cap for when a bigger batch needs more passes to converge.
MAX_EPOCHS = int(os.getenv("NEURALBET_MAX_EPOCHS", "200"))
EARLY_STOP_PATIENCE = int(os.getenv("NEURALBET_EARLY_STOP_PATIENCE", "10"))
# 64 -> 128: fewer, larger mini-batches per epoch use the CPU's vectorized matmuls more
# efficiently (more work per Python-level loop iteration) — meaningful now that each
# epoch sees 10x the samples.
BATCH_SIZE = int(os.getenv("NEURALBET_BATCH_SIZE", "128"))
LEARNING_RATE = float(os.getenv("NEURALBET_LEARNING_RATE", "1e-3"))
GRAD_CLIP_NORM = 1.0

# Weight on the decision-head loss (the bet/no-bet verdict) relative to the win-head BCE.
# 1.0 — same order of magnitude as bce_loss, so early training doesn't let one head starve
# the other of gradient.
DECISION_LOSS_WEIGHT = float(os.getenv("NEURALBET_DECISION_LOSS_WEIGHT", "1.0"))

# No GPU here (torch.cuda.is_available() is False in this container) — everything runs
# on CPU, so thread count is the real lever. Defaults to PyTorch's own heuristic (which
# undercounts on this host — observed torch.get_num_threads() == 6 on a 12-core
# machine); pin it explicitly instead of leaving throughput on the table. Left just
# short of the full core count so the FastAPI/Uvicorn process and the rest of the
# container still get scheduled promptly.
torch.set_num_threads(int(os.getenv("NEURALBET_TORCH_THREADS", str(max((os.cpu_count() or 4) - 2, 1)))))

# How many candidate bets make up one "round" for the bankroll-allocation loss —
# roughly how many live outcomes the bot might be weighing at once in a real cycle.
ROUND_SIZE = 20


SPORT_EMB_DIM = 8
MARKET_EMB_DIM = 6
TEAM_EMB_DIM = 6


class OddsTrajectoryGRU(nn.Module):
    """
    PyTorch GRU sequence model over odds-trajectory + live-score steps, conditioned on
    which sport, which market family, and which two teams/players (team1_idx/team2_idx —
    see app/neuralbet/context.py's team_index) the bet is on.
    Sequence input: (batch, seq_len=10, input_dim=3) — per step: [log(coefficient),
    score_diff, t_norm]. score_diff has a completely different scale/meaning per sport
    (3 goals in football vs. 3 points in basketball), and the sequence alone says
    nothing about what kind of outcome is even being priced or *who* is playing — so
    sport_idx/market_idx/team1_idx/team2_idx are embedded and concatenated onto the
    GRU's final hidden state before the output head, giving the model static context
    the odds curve by itself can't provide. Team identity uses a hashed embedding
    (TEAM_HASH_BUCKETS is far larger than the small closed sport/market vocabularies)
    since there's no fixed roster of every team/player Fonbet will ever cover.
    Output: 4 raw logits per sample — [win_logit, decision_logit, stake_logit,
    exposure_logit]:
      - win_logit: sigmoid() gives the win-probability estimate (same role the old
        single-output head had) — still an input to the LightGBM/PyTorch ensemble blend.
      - decision_logit: sigmoid() gives the model's own confidence in its bet/no-bet
        verdict — >= 0.5 means "this outcome will win," trained with a cost-sensitive
        loss (see train_online) instead of copying win_logit's threshold, so the network
        learns where its own decision boundary should sit rather than inheriting a fixed
        confidence cutoff from outside the model.
      - stake_logit / exposure_logit: only meaningful *relative to other candidates in
        the same betting round* — see bankroll.allocate(). Unused at plain inference.
    No activation is applied here; callers apply sigmoid/softmax as appropriate so the
    raw logits can feed BCEWithLogitsLoss directly (numerically more stable than a
    Sigmoid layer + BCELoss).
    """
    def __init__(self, input_dim: int = 3, hidden_dim: int = HIDDEN_DIM, num_layers: int = GRU_LAYERS):
        super(OddsTrajectoryGRU, self).__init__()
        self.hidden_dim = hidden_dim
        self.gru = nn.GRU(
            input_dim, hidden_dim, num_layers, batch_first=True,
            dropout=0.1 if num_layers > 1 else 0.0,
        )
        self.sport_embed = nn.Embedding(NUM_SPORTS, SPORT_EMB_DIM)
        self.market_embed = nn.Embedding(NUM_MARKET_FAMILIES, MARKET_EMB_DIM)
        # Shared table for both slots (not two separate embeddings) — "this team plays
        # well as an underdog" should mean the same thing whether that team happens to
        # be sitting in the team_1 or team_2 column for a given match.
        self.team_embed = nn.Embedding(TEAM_HASH_BUCKETS, TEAM_EMB_DIM)
        self.fc1 = nn.Linear(hidden_dim + SPORT_EMB_DIM + MARKET_EMB_DIM + 2 * TEAM_EMB_DIM, 32)
        self.relu = nn.ReLU()
        self.head = nn.Linear(32, 4)

    def forward(
        self, x: torch.Tensor, sport_idx: torch.Tensor, market_idx: torch.Tensor,
        team1_idx: torch.Tensor, team2_idx: torch.Tensor,
    ) -> torch.Tensor:
        out, _ = self.gru(x)
        last_step = out[:, -1, :]
        ctx = torch.cat([
            last_step, self.sport_embed(sport_idx), self.market_embed(market_idx),
            self.team_embed(team1_idx), self.team_embed(team2_idx),
        ], dim=-1)
        h = self.relu(self.fc1(ctx))
        return self.head(h)


def _build_sequence(step_pairs: List[Tuple[float, int]]) -> List[List[float]]:
    """
    Turns a list of (coefficient, score_diff) snapshots — in chronological order, one
    per odds update — into the fixed-length (SEQ_LEN, 3) feature sequence the GRU takes.
    Shared by predict_single/predict_batch/train_online so the three call sites can't
    drift out of sync with each other (they used to duplicate this formula three times).

    Coefficient is log-scaled: raw coefficients range ~1.01-50, which saturates a GRU's
    gates; log(coeff) is roughly symmetric around 0 and much better behaved.
    t_norm is the step's position in the sequence (0..1) — a real per-step time signal,
    replacing the old hardcoded literal 45.0 that was identical on every step (dead
    weight the model could never learn anything from).
    """
    pairs = step_pairs if step_pairs else [(1.5, 0)]
    if len(pairs) < SEQ_LEN:
        pairs = [pairs[0]] * (SEQ_LEN - len(pairs)) + pairs
    else:
        pairs = pairs[-SEQ_LEN:]
    n = len(pairs)
    return [
        [math.log(max(float(c), 1.01)), float(sd), (i / (n - 1)) if n > 1 else 1.0]
        for i, (c, sd) in enumerate(pairs)
    ]


def _decision_loss_weights(coefficients: torch.Tensor, targets: torch.Tensor) -> torch.Tensor:
    """
    Per-sample cost weights for the decision-head loss: plain BCE would just make the
    verdict head copy the win head's 0.5 threshold, so instead the price of each mistake
    is what it would actually cost a real bet. Predicting "will win" on a bet that loses
    costs the whole stake (weight 1.0); predicting "will lose" on a bet that wins costs
    the missed profit (coefficient - 1) — a high-odds miss should be penalized harder than
    passing on a near-even-money one, or the head just learns to say "no" to everything
    above 1/coeff. Clamped to >= 0.1 so a coefficient near 1.0 doesn't zero out its
    gradient entirely.
    """
    return torch.where(
        targets >= 0.5,
        torch.clamp(coefficients - 1.0, min=0.1),
        torch.ones_like(coefficients),
    )


class NeuralBetEnsemble:
    """
    Ensemble model combining PyTorch GRU sequence model and LightGBM GBDT.
    Saves and loads weight checkpoints from /app/data/models/ persistent volume.
    """
    def __init__(self):
        self.pytorch_model = OddsTrajectoryGRU()
        self.pytorch_optimizer = optim.AdamW(self.pytorch_model.parameters(), lr=LEARNING_RATE)
        self.is_trained = False

        # Real gradient-boosted classifier, fit on actual resolved bets (see
        # train_lightgbm). None until the first successful training pass — until then
        # predict_single() falls back to the odds-implied heuristic.
        self.lgb_model: Any = None
        self.lgb_trained = False

        # Load weights if checkpoint exists
        self.load_checkpoints()

    def save_checkpoints(self, extra: Optional[Dict[str, Any]] = None):
        try:
            os.makedirs(MODEL_DIR, exist_ok=True)
            payload = {
                "model_state": self.pytorch_model.state_dict(),
                "optimizer_state": self.pytorch_optimizer.state_dict(),
            }
            if extra:
                payload.update(extra)
            torch.save(payload, PYTORCH_WEIGHTS_PATH)
            logger.info(f"Saved PyTorch model weights checkpoint to {PYTORCH_WEIGHTS_PATH}")
        except Exception as e:
            logger.error(f"Error saving model weights: {e}")

    def _load_model_state_soft(self, state_dict: Dict[str, Any]) -> bool:
        """
        Loads a checkpoint's weights, tolerating the head-shape change from adding the
        decision-verdict output (3 logits -> 4). A plain load_state_dict() raises
        RuntimeError on any shape mismatch, and the old code just dropped the whole
        checkpoint on that error — silently resetting training to scratch on this exact
        deploy. Instead, copy every matching tensor as-is, and for `head.weight`/
        `head.bias` specifically, remap the old [win, stake, exposure] rows onto the new
        [win, decision, stake, exposure] layout (old row 0 -> new row 0, old row 1 ->
        new row 2, old row 2 -> new row 3); the new decision row keeps its random init.
        Returns True if a shape remap actually happened (i.e. this is an old-format
        checkpoint) — callers use this to know the saved optimizer momentum buffers for
        `head.weight`/`head.bias` are now stale and must not be reused (see load_checkpoints).
        """
        own_state = self.pytorch_model.state_dict()
        old_to_new_head_rows = [0, 2, 3]
        head_resized = False
        for name, param in state_dict.items():
            if name not in own_state:
                continue
            target = own_state[name]
            if param.shape == target.shape:
                target.copy_(param)
            elif name in ("head.weight", "head.bias") and param.shape[0] == 3 and target.shape[0] == 4:
                with torch.no_grad():
                    for old_idx, new_idx in enumerate(old_to_new_head_rows):
                        target[new_idx].copy_(param[old_idx])
                head_resized = True
            else:
                logger.warning(
                    f"Skipping checkpoint tensor {name}: shape {tuple(param.shape)} "
                    f"does not match model {tuple(target.shape)}."
                )
        self.pytorch_model.load_state_dict(own_state)
        return head_resized

    def load_checkpoints(self):
        try:
            if os.path.exists(PYTORCH_WEIGHTS_PATH):
                blob = torch.load(PYTORCH_WEIGHTS_PATH, map_location="cpu")
                # Backward compat: older checkpoints were a bare state_dict rather than
                # {"model_state": ...}.
                state = blob["model_state"] if isinstance(blob, dict) and "model_state" in blob else blob
                head_resized = self._load_model_state_soft(state)
                # AdamW's load_state_dict() stores per-parameter momentum buffers
                # *positionally*, with no shape check against the live model — a mismatch
                # only blows up later, inside optimizer.step()'s elementwise ops against
                # the (now differently-shaped) head.weight/head.bias gradients, as a
                # "tensor a (3) must match tensor b (4)" RuntimeError on every single
                # training step. So when the head was just remapped to a new shape, skip
                # restoring the optimizer state entirely — a fresh AdamW simply rebuilds
                # its momentum over the next few steps, which is harmless; reusing stale
                # buffers for a resized layer is not.
                if not head_resized and isinstance(blob, dict) and "optimizer_state" in blob:
                    try:
                        self.pytorch_optimizer.load_state_dict(blob["optimizer_state"])
                    except Exception:
                        # Optimizer shape changed (e.g. architecture bump) — keep the
                        # fresh optimizer state, the model weights still loaded fine.
                        pass
                self.is_trained = True
                logger.info(f"Successfully loaded PyTorch model weights from {PYTORCH_WEIGHTS_PATH}")
        except Exception as e:
            logger.error(f"Error loading model weights: {e}")

        try:
            if os.path.exists(LIGHTGBM_MODEL_PATH):
                booster = lgb.Booster(model_file=LIGHTGBM_MODEL_PATH)
                if booster.num_feature() != len(LGB_FEATURE_NAMES):
                    # Feature set changed (e.g. sport_idx added) since this booster was
                    # saved — a mismatched-shape predict() would just crash every
                    # inference cycle. Discard it and fall back to the heuristic score
                    # until the next scheduled refit produces a compatible booster.
                    logger.warning(
                        f"Ignoring saved LightGBM model at {LIGHTGBM_MODEL_PATH}: "
                        f"has {booster.num_feature()} features, expected {len(LGB_FEATURE_NAMES)}."
                    )
                else:
                    self.lgb_model = booster
                    self.lgb_trained = True
                    logger.info(f"Successfully loaded LightGBM model from {LIGHTGBM_MODEL_PATH}")
        except Exception as e:
            logger.error(f"Error loading LightGBM model: {e}")

    def _lgb_feature_row(self, coeff: float, initial_coeff: float, min_coeff: float, max_coeff: float, samples_count: int, factor_id: int, score_diff: int, sport_idx: int = 0, team1_idx: int = 0, team2_idx: int = 0) -> List[float]:
        drop_ratio = (initial_coeff - coeff) / initial_coeff if initial_coeff > 0 else 0.0
        volatility = (max_coeff - min_coeff) if (max_coeff is not None and min_coeff is not None) else 0.0
        return [coeff, initial_coeff, drop_ratio, volatility, float(samples_count or 0), float(factor_id or 0), float(score_diff), float(sport_idx), float(team1_idx), float(team2_idx)]

    def predict_single(
        self,
        step_pairs: List[Tuple[float, int]],
        current_coeff: float,
        initial_coeff: float,
        factor_id: int = 0,
        sport_path: Optional[str] = None,
        team_1: Optional[str] = None,
        team_2: Optional[str] = None,
    ) -> Tuple[float, float, float, float, float, float, float]:
        seq = _build_sequence(step_pairs)
        tensor_in = torch.tensor(seq, dtype=torch.float32).unsqueeze(0)
        sp_idx = sport_index(sport_path)
        mk_idx = market_family_index(factor_id)
        t1_idx = team_index(team_1)
        t2_idx = team_index(team_2)
        sport_tensor = torch.tensor([sp_idx], dtype=torch.long)
        market_tensor = torch.tensor([mk_idx], dtype=torch.long)
        team1_tensor = torch.tensor([t1_idx], dtype=torch.long)
        team2_tensor = torch.tensor([t2_idx], dtype=torch.long)

        self.pytorch_model.eval()
        with torch.no_grad():
            logits = self.pytorch_model(tensor_in, sport_tensor, market_tensor, team1_tensor, team2_tensor)[0]
            pytorch_prob = float(torch.sigmoid(logits[0]).item())
            decision_prob = float(torch.sigmoid(logits[1]).item())
            stake_logit = float(logits[2].item())
            exposure_logit = float(logits[3].item())

        score_diff = step_pairs[-1][1] if step_pairs else 0
        if self.lgb_trained and self.lgb_model is not None:
            coeffs = [c for c, _ in step_pairs] if step_pairs else [current_coeff]
            min_coeff, max_coeff = min(coeffs), max(coeffs)
            row = self._lgb_feature_row(current_coeff, initial_coeff, min_coeff, max_coeff, len(step_pairs), factor_id, score_diff, sp_idx, t1_idx, t2_idx)
            raw_pred = self.lgb_model.predict(np.array([row], dtype=np.float64))[0]
            lgb_score = min(max(float(raw_pred), 0.02), 0.98)
        else:
            # No trained LightGBM model yet (not enough resolved bets) — fall back to an
            # odds-implied heuristic so the app still produces usable numbers.
            implied_prob = (1.0 / current_coeff) if current_coeff > 1.0 else 0.85
            coeff_drop_ratio = (initial_coeff - current_coeff) / initial_coeff if initial_coeff > 0 else 0.0
            trend_boost = coeff_drop_ratio * 0.18
            score_boost = score_diff * 0.025
            lgb_score = min(max(implied_prob + trend_boost + score_boost, 0.12), 0.95)

        ensemble_ratio = 0.35 * pytorch_prob + 0.65 * lgb_score
        win_probability = min(max(ensemble_ratio * 100.0, 12.0), 95.0)
        error_rate = round(100.0 - win_probability, 1)

        return (
            round(win_probability, 1), error_rate, round(lgb_score, 3), round(pytorch_prob, 3),
            round(decision_prob, 3), stake_logit, exposure_logit,
        )

    def predict_batch(self, items: List[Dict[str, Any]]) -> List[Tuple[float, float, float, float, float, float, float]]:
        """
        Same math as predict_single, but for a whole cycle's worth of live odds at once:
        one PyTorch forward pass and one LightGBM predict() call for the entire batch
        instead of thousands of individual Python-level calls. Each item needs a
        `step_pairs` list (chronological (coefficient, score_diff) snapshots, see
        _build_sequence), plus `current_coeff`, `initial_coeff`, `factor_id`,
        `sport_path`, `team_1`, `team_2` (used to condition both models — see
        app/neuralbet/context.py).
        Returns results in the same order as `items`: (win_probability, error_rate,
        lgb_score, pytorch_score, decision_prob, stake_logit, exposure_logit).
        decision_prob is the model's own bet/no-bet verdict confidence (>= 0.5 means
        "will win" — see OddsTrajectoryGRU's docstring); stake_logit/exposure_logit are
        only meaningful for the live-bankroll allocator (bankroll.allocate), not stored in
        ai_predictions.
        """
        if not items:
            return []

        sequences = [_build_sequence(it["step_pairs"]) for it in items]
        x_tensor = torch.tensor(np.array(sequences, dtype=np.float32))
        sport_idxs = [sport_index(it.get("sport_path")) for it in items]
        market_idxs = [market_family_index(it.get("factor_id", 0)) for it in items]
        team1_idxs = [team_index(it.get("team_1")) for it in items]
        team2_idxs = [team_index(it.get("team_2")) for it in items]
        sport_tensor = torch.tensor(sport_idxs, dtype=torch.long)
        market_tensor = torch.tensor(market_idxs, dtype=torch.long)
        team1_tensor = torch.tensor(team1_idxs, dtype=torch.long)
        team2_tensor = torch.tensor(team2_idxs, dtype=torch.long)
        self.pytorch_model.eval()
        with torch.no_grad():
            logits = self.pytorch_model(x_tensor, sport_tensor, market_tensor, team1_tensor, team2_tensor)
            pytorch_probs = torch.sigmoid(logits[:, 0]).tolist()
            decision_probs = torch.sigmoid(logits[:, 1]).tolist()
            stake_logits = logits[:, 2].tolist()
            exposure_logits = logits[:, 3].tolist()

        score_diffs = [(it["step_pairs"][-1][1] if it["step_pairs"] else 0) for it in items]

        if self.lgb_trained and self.lgb_model is not None:
            X = []
            for it, score_diff, sp_idx, t1_idx, t2_idx in zip(items, score_diffs, sport_idxs, team1_idxs, team2_idxs):
                pairs = it["step_pairs"]
                coeffs = [c for c, _ in pairs] if pairs else [it["current_coeff"]]
                min_coeff, max_coeff = min(coeffs), max(coeffs)
                X.append(self._lgb_feature_row(
                    it["current_coeff"], it["initial_coeff"], min_coeff, max_coeff,
                    len(pairs), it.get("factor_id", 0), score_diff, sp_idx, t1_idx, t2_idx,
                ))
            lgb_scores = [min(max(float(p), 0.02), 0.98) for p in self.lgb_model.predict(np.array(X, dtype=np.float64))]
        else:
            lgb_scores = []
            for it, score_diff in zip(items, score_diffs):
                coeff = it["current_coeff"]
                initial_coeff = it["initial_coeff"]
                implied_prob = (1.0 / coeff) if coeff > 1.0 else 0.85
                coeff_drop_ratio = (initial_coeff - coeff) / initial_coeff if initial_coeff > 0 else 0.0
                trend_boost = coeff_drop_ratio * 0.18
                score_boost = score_diff * 0.025
                lgb_scores.append(min(max(implied_prob + trend_boost + score_boost, 0.12), 0.95))

        results = []
        for pytorch_prob, lgb_score, decision_prob, stake_logit, exposure_logit in zip(
            pytorch_probs, lgb_scores, decision_probs, stake_logits, exposure_logits
        ):
            ensemble_ratio = 0.35 * pytorch_prob + 0.65 * lgb_score
            win_probability = min(max(ensemble_ratio * 100.0, 12.0), 95.0)
            error_rate = round(100.0 - win_probability, 1)
            results.append((
                round(win_probability, 1), error_rate, round(lgb_score, 3), round(pytorch_prob, 3),
                round(decision_prob, 3), stake_logit, exposure_logit,
            ))
        return results

    def train_lightgbm(self, rows: List[Dict[str, Any]], val_rows: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        """
        Fits a real binary GBDT classifier on resolved bets: given the odds trajectory
        shape (drop ratio, volatility), sample count, market type and score state at the
        point the bet was cut off, predict whether it actually won. `rows`/`val_rows`
        must already be leak-free (score_diff + coefficient as they stood at the cutoff,
        not the final match state — see pipeline.py) and time-split (val_rows strictly
        later than rows) so the reported accuracy means something.
        """
        usable = [r for r in rows if r.get("is_win") is not None]
        if len(usable) < 50:
            return {"trained": False, "reason": "not_enough_samples", "samples": len(usable)}

        def to_xy(items):
            X, y = [], []
            for r in items:
                initial_coeff = r.get("initial_coefficient") or 1.5
                coeff = r.get("coefficient") or initial_coeff
                row = self._lgb_feature_row(
                    coeff, initial_coeff,
                    r.get("min_coefficient"), r.get("max_coefficient"),
                    r.get("samples_count"), r.get("factor_id"), r.get("score_diff", 0),
                    sport_index(r.get("sport_path")),
                    team_index(r.get("team_1")), team_index(r.get("team_2")),
                )
                X.append(row)
                y.append(float(r["is_win"]))
            return np.array(X, dtype=np.float64), np.array(y, dtype=np.float64)

        X_arr, y_arr = to_xy(usable)
        train_data = lgb.Dataset(X_arr, label=y_arr, feature_name=LGB_FEATURE_NAMES)

        valid_sets = [train_data]
        valid_names = ["train"]
        usable_val = [r for r in (val_rows or []) if r.get("is_win") is not None]
        if len(usable_val) >= 20:
            X_val, y_val = to_xy(usable_val)
            val_data = lgb.Dataset(X_val, label=y_val, feature_name=LGB_FEATURE_NAMES, reference=train_data)
            valid_sets.append(val_data)
            valid_names.append("val")

        params = {
            "objective": "binary",
            "metric": "binary_logloss",
            "verbosity": -1,
            "num_leaves": 15,
            "learning_rate": 0.05,
            "min_data_in_leaf": 10,
        }
        callbacks = [lgb.log_evaluation(period=0)]
        if len(valid_sets) > 1:
            callbacks.append(lgb.early_stopping(stopping_rounds=15, verbose=False))
        booster = lgb.train(
            params, train_data, num_boost_round=200,
            valid_sets=valid_sets, valid_names=valid_names, callbacks=callbacks,
        )

        # Report accuracy on the held-out split when we have one — a train-set accuracy
        # number is not a meaningful "is this model any good" metric.
        eval_X, eval_y = (X_val, y_val) if len(valid_sets) > 1 else (X_arr, y_arr)
        preds = booster.predict(eval_X, num_iteration=booster.best_iteration or None)
        pred_labels = [1.0 if p >= 0.5 else 0.0 for p in preds]
        accuracy = sum(1 for p, t in zip(pred_labels, eval_y) if p == t) / len(eval_y)
        eval_split = "val" if len(valid_sets) > 1 else "train"

        self.lgb_model = booster
        self.lgb_trained = True

        try:
            os.makedirs(MODEL_DIR, exist_ok=True)
            booster.save_model(LIGHTGBM_MODEL_PATH)
            logger.info(f"Saved LightGBM model checkpoint to {LIGHTGBM_MODEL_PATH}")
        except Exception as e:
            logger.error(f"Error saving LightGBM model: {e}")

        importance = dict(zip(LGB_FEATURE_NAMES, [int(v) for v in booster.feature_importance()]))

        return {
            "trained": True,
            "samples": len(usable),
            "val_samples": len(usable_val),
            "eval_split": eval_split,
            "train_accuracy": round(accuracy * 100.0, 1),
            "feature_importance": importance,
        }

    def _prepare_sample(self, sample: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Builds one training example from a resolved bet, cutting its odds/score history
        off at a random point instead of using the whole match — the model only ever
        sees a partial trajectory at real inference time (the match is still live), so
        training must match that (see plan section B3). Returns None for bets with no
        usable history.
        """
        if sample.get("is_win") is None:
            return None
        odds_seq = sample.get("odds_seq") or []
        score_seq = sample.get("score_seq") or []
        if not odds_seq:
            return None
        if len(score_seq) != len(odds_seq):
            # Older archived bets predate score_seq_json — fall back to a flat score_diff.
            fallback = sample.get("score_diff_at_bet", 0)
            score_seq = [fallback] * len(odds_seq)

        n = len(odds_seq)
        cutoff = random.randint(min(3, n), n) if n > 1 else n
        pairs = list(zip(odds_seq[:cutoff], score_seq[:cutoff]))

        return {
            "step_pairs": pairs,
            "target": float(sample["is_win"]),
            "coefficient": float(odds_seq[cutoff - 1]),
            "sport_idx": sport_index(sample.get("sport_path")),
            "market_idx": market_family_index(sample.get("factor_id")),
            "team1_idx": team_index(sample.get("team_1")),
            "team2_idx": team_index(sample.get("team_2")),
        }

    @staticmethod
    def _context_tensors(prepared: List[Dict[str, Any]]) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]:
        sport_t = torch.tensor([p.get("sport_idx", 0) for p in prepared], dtype=torch.long)
        market_t = torch.tensor([p.get("market_idx", 0) for p in prepared], dtype=torch.long)
        team1_t = torch.tensor([p.get("team1_idx", 0) for p in prepared], dtype=torch.long)
        team2_t = torch.tensor([p.get("team2_idx", 0) for p in prepared], dtype=torch.long)
        return sport_t, market_t, team1_t, team2_t

    def _forward_metrics(self, prepared: List[Dict[str, Any]]) -> Tuple[float, float]:
        """
        Returns (decision_loss, guess_rate) for a prepared batch in eval mode, no grad.
        Reads the *decision* head (logits[:, 1]), not the win-probability head — "guessed
        right" now means the model's own bet/no-bet verdict matched the outcome, which is
        the number the training loop selects its best epoch on and the number the UI/logs
        report as accuracy (see train_online).
        """
        if not prepared:
            return float("nan"), float("nan")
        seqs = torch.tensor([_build_sequence(p["step_pairs"]) for p in prepared], dtype=torch.float32)
        targets = torch.tensor([p["target"] for p in prepared], dtype=torch.float32).unsqueeze(1)
        coeffs = torch.tensor([p["coefficient"] for p in prepared], dtype=torch.float32).unsqueeze(1)
        weights = _decision_loss_weights(coeffs, targets)
        sport_t, market_t, team1_t, team2_t = self._context_tensors(prepared)
        self.pytorch_model.eval()
        with torch.no_grad():
            logits = self.pytorch_model(seqs, sport_t, market_t, team1_t, team2_t)[:, 1:2]
            loss = nn.functional.binary_cross_entropy_with_logits(logits, targets, weight=weights).item()
            guess_rate = ((torch.sigmoid(logits) >= 0.5).float() == targets).float().mean().item()
        return loss, guess_rate

    def _bankroll_pass(
        self, prepared: List[Dict[str, Any]], account: str, commit: bool,
    ) -> Dict[str, Any]:
        """
        Replays `prepared` samples in rounds of ROUND_SIZE against the named persistent
        bankroll account, using the model's *current* weights to decide stakes each
        round. If commit=True the results are actually written to bankroll_accounts/
        bankroll_ledger (used once, after the best epoch is chosen, for the "training"
        account — see train_online). If commit=False this only computes the
        differentiable bankroll loss for backprop and does not touch the DB.
        Returns {"loss": tensor-or-None, "bank_end": float, "rounds": int, ...}.
        """
        acc = bankroll.get_account(account)
        bank = acc["balance"]
        start_bank = bank
        losses = []
        total_staked = 0.0
        total_returned = 0.0
        bets_count = 0
        wins_count = 0
        losses_count = 0
        rounds_played = 0
        ruin_events = 0

        # commit=True is a bookkeeping-only replay (writing the chosen epoch's decisions
        # to the DB) — no backward() ever follows it, so skip building an autograd graph.
        grad_ctx = torch.no_grad() if commit else torch.enable_grad()
        with grad_ctx:
            for i in range(0, len(prepared), ROUND_SIZE):
                chunk = prepared[i:i + ROUND_SIZE]
                if len(chunk) < 2:
                    continue
                seqs = torch.tensor([_build_sequence(c["step_pairs"]) for c in chunk], dtype=torch.float32)
                sport_t, market_t, team1_t, team2_t = self._context_tensors(chunk)
                logits = self.pytorch_model(seqs, sport_t, market_t, team1_t, team2_t)
                stake_logits = logits[:, 2]
                exposure_logit = logits[:, 3].mean()
                coeffs = torch.tensor([c["coefficient"] for c in chunk], dtype=torch.float32)
                wins = torch.tensor([c["target"] for c in chunk], dtype=torch.float32)

                fractions = bankroll.allocate(stake_logits, exposure_logit)
                # Only actually risk money on candidates the model's own verdict says will
                # win — see decision_logit in OddsTrajectoryGRU. The mask is a hard,
                # non-differentiable decision (detached), same as allocate()'s own
                # min-stake/max-positions cuts; gradient into stake_logits still flows
                # through the softmax weights of whichever candidates the mask keeps.
                verdict = (torch.sigmoid(logits[:, 1]) >= 0.5).float().detach()
                fractions = fractions * verdict

                gain = bankroll.settle(fractions, coeffs, wins)
                round_loss = -torch.log(torch.clamp(gain, min=0.01))

                bank_next = bank * float(gain.detach().item())
                ruined = bank_next <= bankroll.RUIN_THRESHOLD
                if ruined:
                    round_loss = round_loss + bankroll.RUIN_PENALTY
                    ruin_events += 1

                fr = fractions.detach()
                staked = float((fr * bank).sum().item())
                returned = float((fr * coeffs * wins * bank).detach().sum().item())
                n_bets = int((fr >= bankroll.MIN_STAKE_FRACTION).sum().item())
                round_wins = int(((fr >= bankroll.MIN_STAKE_FRACTION) & (wins >= 0.5)).sum().item())
                round_losses = int(((fr >= bankroll.MIN_STAKE_FRACTION) & (wins < 0.5)).sum().item())

                if commit:
                    bankroll.apply_round_result(
                        account, staked=staked, returned=returned, bets_count=n_bets,
                        wins=round_wins, losses=round_losses,
                    )
                    bank = bankroll.get_account(account)["balance"]
                else:
                    bank = acc["start_balance"] if ruined else bank_next

                losses.append(round_loss)
                total_staked += staked
                total_returned += returned
                bets_count += n_bets
                wins_count += round_wins
                losses_count += round_losses
                rounds_played += 1

        loss_tensor = torch.stack(losses).mean() if losses else None
        return {
            "loss": loss_tensor,
            "bank_start": start_bank,
            "bank_end": bank,
            "rounds": rounds_played,
            "bets_count": bets_count,
            "wins": wins_count,
            "losses": losses_count,
            "total_staked": total_staked,
            "total_returned": total_returned,
            "ruin_events": ruin_events,
        }

    def train_online(
        self,
        training_data: List[Dict[str, Any]],
        val_data: Optional[List[Dict[str, Any]]] = None,
        epochs: int = MAX_EPOCHS,
        on_epoch: Any = None,
    ) -> Dict[str, Any]:
        """
        Online-training pass: mini-batch AdamW over a batch of resolved bets, combining
        three loss terms —
          1. BCE-with-logits on the win/loss label for the win-probability head (still
             feeds the LightGBM/PyTorch ensemble blend used for the displayed percentage),
          2. a cost-sensitive BCE on the *decision* head — the bet/no-bet verdict — whose
             per-sample weight is the coefficient-derived cost of getting that particular
             bet wrong (see _decision_loss_weights). This is what teaches the network to
             say "will win" / "will lose" instead of copying the win head's fixed 0.5
             cutoff, and
          3. a bankroll loss: -log(bank_growth) replayed over rounds of candidates using
             the model's own decision/stake/exposure heads (bankroll.allocate/settle,
             masked by the decision verdict — see _bankroll_pass) — this is what teaches
             the network that betting size matters, not just direction (see plan part C).
             Ruin (bank hits 0) adds a heavy penalty and resets the *virtual* training
             bank used for this loss back to start_balance.
        Runs up to `epochs` passes but keeps the weights from whichever epoch had the
        best held-out (val_data) decision-loss — early-stopping model selection — since
        the hardware budget allows a generous epoch count without every extra epoch
        being pure overfitting. `on_epoch(epoch_index, train_loss, val_loss)` is called
        after every epoch if provided.
        After picking the winning epoch, its allocations are replayed *once more* and
        actually committed to the persistent "training" bankroll account (bankroll.
        apply_round_result), so the logged bank balance reflects real decisions made
        with the weights that were kept, not a training-time snapshot.
        """
        empty_metrics = {
            "samples_used": 0, "samples_skipped": len(training_data), "positive_count": 0,
            "negative_count": 0, "epochs_run": 0, "epoch_losses": [], "initial_loss": None,
            "final_loss": None, "train_guess_rate": None, "val_loss": None, "val_guess_rate": None,
            "best_epoch": None, "bankroll": None,
        }

        # The virtual "training" bank compounds round over round purely for the loss
        # signal, with nothing capping its growth across passes — left alone it drifts
        # into float overflow (inf) after enough online-training passes, which then
        # poisons every future round (inf - inf = NaN, which SQLite stores as NULL and
        # rejects via the NOT NULL constraint). Starting each pass from a clean
        # start_balance keeps it bounded.
        bankroll.reset_account("training")

        prepared = [p for p in (self._prepare_sample(s) for s in training_data) if p is not None]
        skipped = len(training_data) - len(prepared)
        if len(prepared) < 2:
            return {**empty_metrics, "samples_skipped": skipped}

        prepared_val = [p for p in (self._prepare_sample(s) for s in (val_data or [])) if p is not None]

        positive_count = sum(1 for p in prepared if p["target"] == 1.0)
        negative_count = len(prepared) - positive_count
        pos_weight = torch.tensor([negative_count / max(positive_count, 1)], dtype=torch.float32) if positive_count else None
        bce = nn.BCEWithLogitsLoss(pos_weight=pos_weight)

        best_val = float("inf")
        best_state = None
        best_epoch = 0
        epochs_without_improvement = 0
        epoch_losses: List[float] = []

        self.pytorch_model.train()
        for epoch_idx in range(1, epochs + 1):
            order = list(range(len(prepared)))
            random.shuffle(order)
            epoch_train_losses = []

            for start in range(0, len(order), BATCH_SIZE):
                batch_idx = order[start:start + BATCH_SIZE]
                batch = [prepared[i] for i in batch_idx]
                seqs = torch.tensor([_build_sequence(b["step_pairs"]) for b in batch], dtype=torch.float32)
                targets = torch.tensor([b["target"] for b in batch], dtype=torch.float32).unsqueeze(1)
                sport_t, market_t, team1_t, team2_t = self._context_tensors(batch)

                self.pytorch_optimizer.zero_grad()
                logits = self.pytorch_model(seqs, sport_t, market_t, team1_t, team2_t)
                win_logits = logits[:, 0:1]
                bce_loss = bce(win_logits, targets)

                # Decision-head loss: cost-weighted so the network learns its own
                # bet/no-bet boundary instead of inheriting the win-head's 0.5 cutoff —
                # see _decision_loss_weights. This is also what selection_metric/train_loss
                # below track, since "guessed right" is judged on this head, not win_logits.
                decision_logits = logits[:, 1:2]
                coeffs_t = torch.tensor([b["coefficient"] for b in batch], dtype=torch.float32).unsqueeze(1)
                decision_weights = _decision_loss_weights(coeffs_t, targets)
                decision_loss = nn.functional.binary_cross_entropy_with_logits(
                    decision_logits, targets, weight=decision_weights,
                )

                loss = bce_loss + DECISION_LOSS_WEIGHT * decision_loss

                bank_pass = self._bankroll_pass(batch, account="training", commit=False)
                if bank_pass["loss"] is not None:
                    loss = loss + bankroll.BANKROLL_LOSS_WEIGHT * bank_pass["loss"]

                loss.backward()
                torch.nn.utils.clip_grad_norm_(self.pytorch_model.parameters(), GRAD_CLIP_NORM)
                self.pytorch_optimizer.step()
                epoch_train_losses.append(float(decision_loss.item()))

            train_loss = sum(epoch_train_losses) / max(len(epoch_train_losses), 1)
            epoch_losses.append(train_loss)

            val_loss, val_guess_rate = self._forward_metrics(prepared_val) if prepared_val else (train_loss, None)
            selection_metric = val_loss if prepared_val else train_loss

            logger.info(
                f"PyTorch online training epoch {epoch_idx}/{epochs} — "
                f"train_loss: {train_loss:.4f}, val_loss: {val_loss:.4f}"
            )
            if on_epoch is not None:
                try:
                    on_epoch(epoch_idx, train_loss, val_loss if prepared_val else None)
                except Exception:
                    pass

            if selection_metric < best_val - 1e-4:
                best_val = selection_metric
                best_state = copy.deepcopy(self.pytorch_model.state_dict())
                best_epoch = epoch_idx
                epochs_without_improvement = 0
            else:
                epochs_without_improvement += 1
                if epochs_without_improvement >= EARLY_STOP_PATIENCE:
                    break

            self.pytorch_model.train()

        if best_state is not None:
            self.pytorch_model.load_state_dict(best_state)

        final_val_loss, final_val_guess_rate = self._forward_metrics(prepared_val) if prepared_val else (None, None)
        final_train_loss, final_train_guess_rate = self._forward_metrics(prepared)

        # Commit the winning epoch's bankroll behavior for real, once.
        bank_result = self._bankroll_pass(prepared, account="training", commit=True)

        self.is_trained = True
        self.pytorch_model.train()

        logger.info(
            f"PyTorch online training complete: best epoch {best_epoch}/{len(epoch_losses)}, "
            f"train_loss {final_train_loss:.4f}, val_loss "
            f"{final_val_loss if final_val_loss is not None else float('nan'):.4f}, "
            f"training bankroll {bank_result['bank_start']:.1f} -> {bank_result['bank_end']:.1f}."
        )

        self.save_checkpoints(extra={"best_epoch": best_epoch})

        return {
            "samples_used": len(prepared),
            "samples_skipped": skipped,
            "positive_count": positive_count,
            "negative_count": negative_count,
            "epochs_run": len(epoch_losses),
            "epoch_losses": [round(l, 4) for l in epoch_losses],
            "initial_loss": round(epoch_losses[0], 4) if epoch_losses else None,
            "final_loss": round(final_train_loss, 4),
            "train_guess_rate": round(final_train_guess_rate * 100.0, 1),
            "val_loss": round(final_val_loss, 4) if final_val_loss is not None else None,
            "val_guess_rate": round(final_val_guess_rate * 100.0, 1) if final_val_guess_rate is not None else None,
            "best_epoch": best_epoch,
            "bankroll": {
                "start": round(bank_result["bank_start"], 2),
                "end": round(bank_result["bank_end"], 2),
                "rounds": bank_result["rounds"],
                "bets_count": bank_result["bets_count"],
                "wins": bank_result["wins"],
                "losses": bank_result["losses"],
                "ruin_events": bank_result["ruin_events"],
            },
        }
