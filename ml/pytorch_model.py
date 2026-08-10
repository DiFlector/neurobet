"""
PyTorch Deep Neural Network Predictor for Sports Odds
Multi-head Neural Network architecture with clean line-by-line terminal progress logging.
"""

import time
import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import TensorDataset, DataLoader
from typing import Tuple, Dict, Any, List

class OddsNet(nn.Module):
    def __init__(self, input_dim: int):
        super(OddsNet, self).__init__()
        self.backbone = nn.Sequential(
            nn.Linear(input_dim, 128),
            nn.LayerNorm(128),
            nn.SiLU(),
            nn.Dropout(0.2),
            nn.Linear(128, 64),
            nn.LayerNorm(64),
            nn.SiLU(),
            nn.Dropout(0.1)
        )
        # Head 1: 1X2 Multi-class classification (Win 1, Draw, Win 2)
        self.head_1x2 = nn.Linear(64, 3)
        # Head 2: Total Over Binary classification
        self.head_over = nn.Linear(64, 1)

    def forward(self, x: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor]:
        features = self.backbone(x)
        logits_1x2 = self.head_1x2(features)
        logits_over = self.head_over(features)
        return logits_1x2, logits_over

class PyTorchOddsPredictor:
    def __init__(self, input_dim: int, lr: float = 1e-3):
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.model = OddsNet(input_dim).to(self.device)
        self.optimizer = optim.AdamW(self.model.parameters(), lr=lr, weight_decay=1e-4)
        self.criterion_1x2 = nn.CrossEntropyLoss()
        self.criterion_over = nn.BCEWithLogitsLoss()
        self.is_trained = False

    def train(self, X_train: np.ndarray, y_1x2_train: np.ndarray, y_over_train: np.ndarray, epochs: int = 15, batch_size: int = 512):
        """Trains PyTorch OddsNet with clean line-by-line terminal logs."""
        self.model.train()
        
        X_clean = np.nan_to_num(X_train, nan=0.0)

        tensor_x = torch.tensor(X_clean, dtype=torch.float32)
        tensor_y_1x2 = torch.tensor(y_1x2_train, dtype=torch.long)
        tensor_y_over = torch.tensor(y_over_train, dtype=torch.float32).unsqueeze(1)

        dataset = TensorDataset(tensor_x, tensor_y_1x2, tensor_y_over)
        drop_last = (len(X_train) > batch_size)
        loader = DataLoader(dataset, batch_size=min(batch_size, len(X_train)), shuffle=True, drop_last=drop_last)

        print(f"\n🧠 Начинаем обучение PyTorch OddsNet ({epochs} эпох, размер батча={batch_size}, устройство={self.device})...", flush=True)
        t_start = time.time()

        for epoch in range(1, epochs + 1):
            t_ep0 = time.time()
            total_loss = 0.0
            num_batches = 0

            for bx, by_1x2, by_over in loader:
                bx, by_1x2, by_over = bx.to(self.device), by_1x2.to(self.device), by_over.to(self.device)

                self.optimizer.zero_grad()
                logits_1x2, logits_over = self.model(bx)

                loss_1x2 = self.criterion_1x2(logits_1x2, by_1x2)
                loss_over = self.criterion_over(logits_over, by_over)
                loss = loss_1x2 + loss_over

                loss.backward()
                self.optimizer.step()

                total_loss += loss.item()
                num_batches += 1

            t_ep1 = time.time()
            ep_time = t_ep1 - t_ep0
            avg_loss = total_loss / max(1, num_batches)
            
            elapsed = t_ep1 - t_start
            rem_epochs = epochs - epoch
            avg_ep_time = elapsed / epoch
            eta_sec = avg_ep_time * rem_epochs

            print(
                f" 🧠 [Эпоха {epoch:02d}/{epochs:02d}] "
                f"| Loss: {avg_loss:7.4f} "
                f"| Время эпохи: {ep_time:4.2f}s "
                f"| Прошло: {elapsed:5.1f}s "
                f"| Осталось (ETA): {eta_sec:5.1f}s",
                flush=True
            )

        self.is_trained = True

    def predict_proba(self, X: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        if not self.is_trained:
            raise RuntimeError("PyTorch model is not trained yet!")

        self.model.eval()
        X_clean = np.nan_to_num(X, nan=0.0)
        tensor_x = torch.tensor(X_clean, dtype=torch.float32).to(self.device)

        with torch.no_grad():
            logits_1x2, logits_over = self.model(tensor_x)
            probs_1x2 = torch.softmax(logits_1x2, dim=1).cpu().numpy()
            probs_over_val = torch.sigmoid(logits_over).cpu().numpy()
            probs_over = np.hstack([1.0 - probs_over_val, probs_over_val])

        return probs_1x2, probs_over

    def save(self, filepath: str):
        torch.save(self.model.state_dict(), filepath)

    def load(self, filepath: str, input_dim: int):
        self.model = OddsNet(input_dim).to(self.device)
        self.model.load_state_dict(torch.load(filepath, map_location=self.device))
        self.is_trained = True
