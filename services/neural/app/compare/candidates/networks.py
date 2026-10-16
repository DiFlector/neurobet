"""Small torch networks for the offline tennis comparison.

Widths and regularization are chosen on the validation matches. The frozen
test is rejected if a caller passes its ids into a fit.
"""

from typing import Optional, Sequence

import numpy as np
from sklearn.preprocessing import StandardScaler

from ..rows import CompareRow, feature_vector
from .ensemble import apply_market_offset, logit

SEED = 42
MAX_STEPS = 12


def torch_device():
    import torch

    if not torch.cuda.is_available():
        return torch.device("cpu"), "cpu", None
    major, minor = torch.cuda.get_device_capability()
    name = torch.cuda.get_device_name(0)
    if major < 7:
        return torch.device("cpu"), "cpu", f"{name} sm_{major}{minor} is not used by current kernels"
    return torch.device("cuda"), "cuda", name


def _guard(rows: Sequence[CompareRow], forbidden: Optional[set]) -> None:
    if forbidden and {row.event_id for row in rows} & set(forbidden):
        raise RuntimeError("Подбор увидел матчи теста.")


def _matrix(rows: Sequence[CompareRow]) -> tuple:
    x = np.asarray([feature_vector(row) for row in rows], dtype=np.float32)
    y = np.asarray([row.label for row in rows], dtype=np.float32)
    counts = {}
    for row in rows:
        counts[row.event_id] = counts.get(row.event_id, 0) + 1
    weights = np.asarray([1.0 / counts[row.event_id] for row in rows], dtype=np.float32)
    market = np.asarray([row.market_prob_a for row in rows], dtype=np.float32)
    return x, y, weights, market


def _match_loss(rows: Sequence[CompareRow], probs: np.ndarray) -> float:
    grouped = {}
    for row, prob in zip(rows, probs):
        p = min(1.0 - 1e-6, max(1e-6, float(prob)))
        loss = -(row.label * np.log(p) + (1 - row.label) * np.log(1 - p))
        grouped.setdefault(row.event_id, []).append(loss)
    if not grouped:
        return 1.0
    return float(np.mean([np.mean(values) for values in grouped.values()]))


class _MLP(object):
    def __new__(cls, widths, dropout):
        import torch
        from torch import nn

        class Net(nn.Module):
            def __init__(self):
                super().__init__()
                layers = []
                previous = 13
                for width in widths:
                    layers.extend([nn.Linear(previous, width), nn.ReLU(), nn.Dropout(dropout)])
                    previous = width
                layers.append(nn.Linear(previous, 1))
                self.net = nn.Sequential(*layers)

            def forward(self, batch):
                return self.net(batch).squeeze(-1)

        return Net()


class _GRU(object):
    def __new__(cls, hidden):
        import torch
        from torch import nn

        class Net(nn.Module):
            def __init__(self):
                super().__init__()
                self.gru = nn.GRU(input_size=13, hidden_size=hidden, batch_first=True)
                self.head = nn.Linear(hidden, 1)

            def forward(self, batch, lengths):
                packed = nn.utils.rnn.pack_padded_sequence(
                    batch, lengths.cpu(), batch_first=True, enforce_sorted=False
                )
                _out, hidden_state = self.gru(packed)
                return self.head(hidden_state[-1]).squeeze(-1)

        return Net()


def _seed(torch_module) -> None:
    torch_module.manual_seed(SEED)
    if torch_module.cuda.is_available():
        torch_module.cuda.manual_seed_all(SEED)


def _fit_tabular(rows, validation, widths, dropout, weight_decay, max_epochs, residual: bool):
    import torch
    from torch.utils.data import DataLoader, TensorDataset

    device, device_name, device_note = torch_device()
    _seed(torch)
    torch.set_num_threads(2)
    x, y, weights, market = _matrix(rows)
    scaler = StandardScaler().fit(x)
    scaled = scaler.transform(x).astype(np.float32)
    model = _MLP(widths, dropout).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=1e-3, weight_decay=weight_decay)
    dataset = TensorDataset(
        torch.from_numpy(scaled),
        torch.from_numpy(y),
        torch.from_numpy(weights),
        torch.from_numpy(market),
    )
    loader = DataLoader(dataset, batch_size=256, shuffle=True)
    best_state = None
    best_loss = None
    waited = 0
    stopped = 0
    for epoch in range(max_epochs):
        model.train()
        for batch_x, batch_y, batch_w, batch_m in loader:
            batch_x = batch_x.to(device)
            batch_y = batch_y.to(device)
            batch_w = batch_w.to(device)
            optimizer.zero_grad()
            raw = model(batch_x)
            logits = _residual_logits(raw, batch_m.to(device)) if residual else raw
            loss = torch.nn.functional.binary_cross_entropy_with_logits(logits, batch_y, reduction="none")
            loss = (loss * batch_w).sum() / batch_w.sum().clamp_min(1e-6)
            loss.backward()
            optimizer.step()
        stopped = epoch + 1
        held = _predict_tabular(model, scaler, validation or rows, device, residual)
        loss_value = _match_loss(validation or rows, held)
        if best_loss is None or loss_value < best_loss - 1e-4:
            best_loss = loss_value
            best_state = {key: value.detach().cpu().clone() for key, value in model.state_dict().items()}
            waited = 0
        else:
            waited += 1
            if waited >= 4:
                break
    model.load_state_dict(best_state)
    return {
        "model": model,
        "scaler": scaler,
        "device": device,
        "device_name": device_name,
        "device_note": device_note,
        "epochs": stopped,
        "parameters": int(sum(item.numel() for item in model.parameters())),
        "train_loss": None if best_loss is None else round(float(best_loss), 6),
    }


def _residual_logits(raw, market):
    import torch

    base = torch.log(market.clamp(1e-4, 1 - 1e-4) / (1 - market.clamp(1e-4, 1 - 1e-4)))
    return base + raw.clamp(-0.35, 0.35)


def _predict_tabular(model, scaler, rows, device, residual: bool) -> np.ndarray:
    import torch

    if not rows:
        return np.asarray([], dtype=float)
    x, _y, _w, market = _matrix(rows)
    model.eval()
    with torch.no_grad():
        batch = torch.from_numpy(scaler.transform(x).astype(np.float32)).to(device)
        raw = model(batch)
        if residual:
            logits = _residual_logits(raw, torch.from_numpy(market).to(device))
        else:
            logits = raw
        probs = torch.sigmoid(logits).detach().cpu().numpy()
    return np.asarray(probs, dtype=float)


def _prefix_samples(rows: Sequence[CompareRow], scaler: StandardScaler):
    """One sequence per row, containing only snapshots observed at or before that row."""
    grouped = {}
    for index, row in enumerate(rows):
        grouped.setdefault(row.event_id, []).append((index, row))
    features = []
    labels = []
    lengths = []
    weights = []
    positions = []
    for items in grouped.values():
        items = sorted(items, key=lambda item: (item[1].observed_at, item[0]))
        for end, (position, row) in enumerate(items):
            prefix = [item for _index, item in items[: end + 1]]
            picked = [prefix[index] for index in _thin(len(prefix))]
            matrix = scaler.transform(np.asarray([feature_vector(item) for item in picked], dtype=np.float32))
            padded = np.zeros((MAX_STEPS, 13), dtype=np.float32)
            padded[: len(picked)] = matrix
            features.append(padded)
            labels.append(float(row.label))
            lengths.append(len(picked))
            weights.append(1.0 / len(items))
            positions.append(position)
    if not features:
        return (
            np.zeros((0, MAX_STEPS, 13), dtype=np.float32),
            np.zeros(0, dtype=np.float32),
            np.zeros(0, dtype=np.int64),
            np.zeros(0, dtype=np.float32),
            np.zeros(0, dtype=np.int64),
        )
    return (
        np.asarray(features, dtype=np.float32),
        np.asarray(labels, dtype=np.float32),
        np.asarray(lengths, dtype=np.int64),
        np.asarray(weights, dtype=np.float32),
        np.asarray(positions, dtype=np.int64),
    )


def _sequences(rows: Sequence[CompareRow], scaler: StandardScaler):
    grouped = {}
    order = []
    for row in rows:
        if row.event_id not in grouped:
            order.append(row.event_id)
            grouped[row.event_id] = []
        grouped[row.event_id].append(row)
    features = []
    labels = []
    lengths = []
    markets = []
    for event_id in order:
        items = sorted(grouped[event_id], key=lambda item: item.observed_at)
        picked = [items[index] for index in _thin(len(items))]
        matrix = scaler.transform(np.asarray([feature_vector(item) for item in picked], dtype=np.float32))
        padded = np.zeros((MAX_STEPS, 13), dtype=np.float32)
        padded[: len(picked)] = matrix
        features.append(padded)
        labels.append(float(picked[-1].label))
        lengths.append(len(picked))
        markets.append(float(picked[-1].market_prob_a))
    return (
        np.asarray(features, dtype=np.float32),
        np.asarray(labels, dtype=np.float32),
        np.asarray(lengths, dtype=np.int64),
        np.asarray(markets, dtype=np.float32),
        order,
    )


def _thin(count: int):
    if count <= MAX_STEPS:
        return list(range(count))
    chosen = []
    for slot in range(MAX_STEPS):
        index = int(round(slot * (count - 1) / (MAX_STEPS - 1)))
        if not chosen or chosen[-1] != index:
            chosen.append(index)
    return chosen


def _fit_gru(rows, validation, hidden, max_epochs):
    import torch
    from torch.utils.data import DataLoader, TensorDataset

    device, device_name, device_note = torch_device()
    _seed(torch)
    torch.set_num_threads(2)
    scaler = StandardScaler().fit(np.asarray([feature_vector(row) for row in rows], dtype=np.float32))
    x, y, lengths, weights, _positions = _prefix_samples(rows, scaler)
    model = _GRU(hidden).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=1e-3, weight_decay=1e-3)
    loader = DataLoader(
        TensorDataset(torch.from_numpy(x), torch.from_numpy(y), torch.from_numpy(lengths), torch.from_numpy(weights)),
        batch_size=64,
        shuffle=True,
    )
    best_state = None
    best_loss = None
    waited = 0
    stopped = 0
    for epoch in range(max_epochs):
        model.train()
        for batch_x, batch_y, batch_len, batch_w in loader:
            optimizer.zero_grad()
            logits = model(batch_x.to(device), batch_len.to(device))
            batch_w = batch_w.to(device)
            loss = torch.nn.functional.binary_cross_entropy_with_logits(logits, batch_y.to(device), reduction="none")
            loss = (loss * batch_w).sum() / batch_w.sum().clamp_min(1e-6)
            loss.backward()
            optimizer.step()
        stopped = epoch + 1
        watched = validation or rows
        probs = _predict_gru(model, scaler, watched, device)
        loss_value = _match_loss(watched, probs)
        if best_loss is None or loss_value < best_loss - 1e-4:
            best_loss = loss_value
            best_state = {key: value.detach().cpu().clone() for key, value in model.state_dict().items()}
            waited = 0
        else:
            waited += 1
            if waited >= 4:
                break
    model.load_state_dict(best_state)
    return {
        "model": model,
        "scaler": scaler,
        "device": device,
        "device_name": device_name,
        "device_note": device_note,
        "epochs": stopped,
        "parameters": int(sum(item.numel() for item in model.parameters())),
        "hidden": hidden,
        "train_loss": None if best_loss is None else round(float(best_loss), 6),
    }


def _predict_gru(model, scaler, rows, device) -> np.ndarray:
    import torch

    if not rows:
        return np.asarray([], dtype=float)
    x, _y, lengths, _weights, positions = _prefix_samples(rows, scaler)
    model.eval()
    with torch.no_grad():
        logits = model(torch.from_numpy(x).to(device), torch.from_numpy(lengths).to(device))
        probs = torch.sigmoid(logits).detach().cpu().numpy()
    aligned = np.empty(len(rows), dtype=float)
    aligned[positions] = probs
    return aligned


class MLPCandidate:
    name = "N_mlp"
    configs = (
        {"widths": (16, 8), "dropout": 0.1, "weight_decay": 1e-3},
        {"widths": (32, 16), "dropout": 0.1, "weight_decay": 1e-3},
        {"widths": (16, 8), "dropout": 0.3, "weight_decay": 1e-2},
    )

    def __init__(self, max_epochs: int = 30):
        self.max_epochs = max_epochs
        self.bundle = None
        self.chosen = None
        self.trials = []
        self.forbidden = set()

    def select(self, train: Sequence[CompareRow], validation: Sequence[CompareRow]) -> None:
        _guard(train, self.forbidden)
        _guard(validation, self.forbidden)
        best = None
        best_loss = None
        for config in self.configs:
            bundle = _fit_tabular(
                train, validation, config["widths"], config["dropout"], config["weight_decay"], self.max_epochs, False
            )
            probs = _predict_tabular(bundle["model"], bundle["scaler"], validation, bundle["device"], False)
            loss = _match_loss(validation, probs)
            trial = {**config, "widths": list(config["widths"]), "log_loss": round(loss, 6), "epochs": bundle["epochs"]}
            self.trials.append(trial)
            if best_loss is None or loss < best_loss:
                best_loss = loss
                best = bundle
                self.chosen = trial
        self.bundle = best

    def predict(self, rows: Sequence[CompareRow]) -> np.ndarray:
        return _predict_tabular(self.bundle["model"], self.bundle["scaler"], rows, self.bundle["device"], False)


class ResidualMLPCandidate:
    name = "N_residual_mlp"
    configs = (
        {"widths": (16, 8), "dropout": 0.1, "weight_decay": 1e-3},
        {"widths": (32, 16), "dropout": 0.1, "weight_decay": 1e-3},
    )

    def __init__(self, max_epochs: int = 30):
        self.max_epochs = max_epochs
        self.bundle = None
        self.chosen = None
        self.trials = []
        self.forbidden = set()

    def select(self, train: Sequence[CompareRow], validation: Sequence[CompareRow]) -> None:
        _guard(train, self.forbidden)
        _guard(validation, self.forbidden)
        best = None
        best_loss = None
        for config in self.configs:
            bundle = _fit_tabular(
                train, validation, config["widths"], config["dropout"], config["weight_decay"], self.max_epochs, True
            )
            probs = _predict_tabular(bundle["model"], bundle["scaler"], validation, bundle["device"], True)
            loss = _match_loss(validation, probs)
            trial = {**config, "widths": list(config["widths"]), "log_loss": round(loss, 6), "epochs": bundle["epochs"]}
            self.trials.append(trial)
            if best_loss is None or loss < best_loss:
                best_loss = loss
                best = bundle
                self.chosen = trial
        self.bundle = best

    def predict(self, rows: Sequence[CompareRow]) -> np.ndarray:
        predicted = _predict_tabular(self.bundle["model"], self.bundle["scaler"], rows, self.bundle["device"], True)
        market = np.asarray([row.market_prob_a for row in rows], dtype=float)
        shift = np.abs(logit(predicted) - logit(market))
        if rows and float(np.nanmax(shift)) > 0.35 + 1e-5:
            return apply_market_offset(market, np.clip(logit(predicted) - logit(market), -0.35, 0.35))
        return predicted


class GRUCandidate:
    name = "N_gru"
    widths = (8, 16)

    def __init__(self, max_epochs: int = 30):
        self.max_epochs = max_epochs
        self.bundle = None
        self.chosen = None
        self.trials = []
        self.forbidden = set()

    def select(self, train: Sequence[CompareRow], validation: Sequence[CompareRow]) -> None:
        _guard(train, self.forbidden)
        _guard(validation, self.forbidden)
        best = None
        best_loss = None
        for hidden in self.widths:
            bundle = _fit_gru(train, validation, hidden, self.max_epochs)
            probs = _predict_gru(bundle["model"], bundle["scaler"], validation, bundle["device"])
            loss = _match_loss(validation, probs)
            trial = {"hidden": hidden, "log_loss": round(loss, 6), "epochs": bundle["epochs"]}
            self.trials.append(trial)
            if best_loss is None or loss < best_loss:
                best_loss = loss
                best = bundle
                self.chosen = trial
        self.bundle = best

    def predict(self, rows: Sequence[CompareRow]) -> np.ndarray:
        return _predict_gru(self.bundle["model"], self.bundle["scaler"], rows, self.bundle["device"])
