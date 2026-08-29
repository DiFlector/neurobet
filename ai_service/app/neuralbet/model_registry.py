"""Named model registry: import/export .nbmodel.zip, activate, list."""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import unicodedata
import zipfile
from neurobet_time import now_moscow_iso

from app.config import (
    ACTIVE_MODEL_PATH,
    DEPLOY_MODE,
    IS_DEV,
    MODEL_DIR,
    REGISTRY_DIR,
)

MODEL_WEIGHT_FILES = ("pytorch_gru.pt", "lightgbm_model.txt")
MODEL_OPTIONAL_FILES = ("lightgbm_meta.json",)
NBMODEL_FORMAT_VERSION = 1

_CYRILLIC = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def _now_iso() -> str:
    return now_moscow_iso()


def slugify(name: str) -> str:
    text = (name or "").strip().lower()
    out: list[str] = []
    for ch in text:
        if ch in _CYRILLIC:
            out.append(_CYRILLIC[ch])
        elif ch in _CYRILLIC.values():
            out.append(ch)
        else:
            out.append(ch)
    text = "".join(out)
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = text.strip("-")
    return text or "model"


def _unique_slug(base: str) -> str:
    slug = slugify(base)
    if not os.path.isdir(os.path.join(REGISTRY_DIR, slug)):
        return slug
    n = 2
    while os.path.isdir(os.path.join(REGISTRY_DIR, f"{slug}-{n}")):
        n += 1
    return f"{slug}-{n}"


def _read_json(path: str) -> Optional[dict]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _write_json(path: str, payload: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _registry_entry_dir(slug: str) -> str:
    return os.path.join(REGISTRY_DIR, slug)


def _read_manifest(slug: str) -> Optional[dict]:
    return _read_json(os.path.join(_registry_entry_dir(slug), "manifest.json"))


RUNTIME_DEV_SLUG = "runtime-dev"
COLD_START_PATH = os.path.join(MODEL_DIR, "cold_start.json")
PYTORCH_WEIGHTS_PATH = os.path.join(MODEL_DIR, "pytorch_gru.pt")


def get_active_model() -> Optional[dict]:
    data = _read_json(ACTIVE_MODEL_PATH)
    if not data or not data.get("slug"):
        return None
    manifest = _read_manifest(str(data["slug"]))
    if manifest:
        return {**manifest, "active": True, "activated_at": data.get("activated_at")}
    return data


def get_public_active_model() -> Optional[dict]:
    """Model name for the public neurobets page — registry entry or dev runtime fallback."""
    bootstrap_legacy_if_needed()
    active = get_active_model()
    if active:
        return {
            "name": active.get("name") or active.get("slug"),
            "slug": active.get("slug"),
            "runtime": False,
        }

    if os.path.isfile(PYTORCH_WEIGHTS_PATH):
        bootstrap_legacy_if_needed()
        active = get_active_model()
        if active:
            return {
                "name": active.get("name") or active.get("slug"),
                "slug": active.get("slug"),
                "runtime": False,
            }
        return {"name": "Legacy (current)", "slug": "legacy-current", "runtime": True}

    if not IS_DEV:
        return None

    cold = _read_json(COLD_START_PATH) or {}
    if cold.get("active"):
        epoch = int(cold.get("epoch") or 1)
        total = int(cold.get("epochs_total") or 2)
        return {
            "name": f"Cold-start (эпоха {epoch}/{total})",
            "slug": RUNTIME_DEV_SLUG,
            "runtime": True,
        }

    return {
        "name": "Runtime (обучение)",
        "slug": RUNTIME_DEV_SLUG,
        "runtime": True,
    }


def _collect_metrics() -> dict:
    metrics: dict[str, Any] = {}
    health_path = os.path.join(MODEL_DIR, "training_health.json")
    health = _read_json(health_path)
    if health:
        signals = health.get("signals") or {}
        if signals.get("val_brier") is not None:
            metrics["val_brier"] = signals.get("val_brier")
    bt_dir = os.path.join(MODEL_DIR, "backtests")
    hist_path = os.path.join(bt_dir, "history.json")
    hist = _read_json(hist_path)
    if isinstance(hist, list) and hist:
        latest = hist[0] if isinstance(hist[0], dict) else None
        if latest:
            overall = latest.get("overall") or {}
            current = overall.get("current") or {}
            if current.get("roi_pct") is not None:
                metrics["backtest_roi_pct"] = current.get("roi_pct")
            if current.get("brier") is not None:
                metrics["backtest_brier"] = current.get("brier")
    return metrics


def list_models() -> list[dict]:
    bootstrap_legacy_if_needed()
    active = _read_json(ACTIVE_MODEL_PATH) or {}
    active_slug = active.get("slug")
    out: list[dict] = []
    if not os.path.isdir(REGISTRY_DIR):
        return out
    for name in sorted(os.listdir(REGISTRY_DIR)):
        path = os.path.join(REGISTRY_DIR, name)
        if not os.path.isdir(path):
            continue
        manifest = _read_manifest(name)
        if not manifest:
            continue
        out.append({
            **manifest,
            "slug": manifest.get("slug") or name,
            "active": name == active_slug or manifest.get("slug") == active_slug,
        })
    out.sort(key=lambda m: m.get("exported_at") or m.get("imported_at") or "", reverse=True)
    return out


def bootstrap_legacy_if_needed() -> Optional[dict]:
    """If runtime weights exist but registry is empty, register legacy-current."""
    os.makedirs(REGISTRY_DIR, exist_ok=True)
    if os.listdir(REGISTRY_DIR):
        return None
    gru = os.path.join(MODEL_DIR, "pytorch_gru.pt")
    if not os.path.exists(gru):
        return None
    slug = "legacy-current"
    dest = _registry_entry_dir(slug)
    os.makedirs(dest, exist_ok=True)
    manifest = {
        "format_version": NBMODEL_FORMAT_VERSION,
        "name": "Legacy (current)",
        "slug": slug,
        "imported_at": _now_iso(),
        "source": DEPLOY_MODE,
        "metrics": _collect_metrics(),
    }
    for fname in MODEL_WEIGHT_FILES + MODEL_OPTIONAL_FILES:
        src = os.path.join(MODEL_DIR, fname)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(dest, fname))
    _write_json(os.path.join(dest, "manifest.json"), manifest)
    if not os.path.exists(ACTIVE_MODEL_PATH):
        _write_json(ACTIVE_MODEL_PATH, {
            "slug": slug,
            "name": manifest["name"],
            "activated_at": _now_iso(),
        })
    return manifest


def _copy_runtime_to_dir(dest_dir: str) -> None:
    os.makedirs(dest_dir, exist_ok=True)
    for fname in MODEL_WEIGHT_FILES + MODEL_OPTIONAL_FILES:
        src = os.path.join(MODEL_DIR, fname)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(dest_dir, fname))


def _copy_dir_to_runtime(src_dir: str) -> None:
    os.makedirs(MODEL_DIR, exist_ok=True)
    for fname in MODEL_WEIGHT_FILES + MODEL_OPTIONAL_FILES:
        src = os.path.join(src_dir, fname)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(MODEL_DIR, fname))


def _validate_model_dir(model_dir: str) -> None:
    gru = os.path.join(model_dir, "pytorch_gru.pt")
    if not os.path.isfile(gru):
        raise ValueError("Missing pytorch_gru.pt in model package")


def _build_manifest(name: str, slug: str, source: Optional[str] = None) -> dict:
    return {
        "format_version": NBMODEL_FORMAT_VERSION,
        "name": name,
        "slug": slug,
        "exported_at": _now_iso(),
        "source": source or DEPLOY_MODE,
        "metrics": _collect_metrics(),
    }


def register_from_dir(model_dir: str, name: str, slug: Optional[str] = None) -> dict:
    _validate_model_dir(model_dir)
    final_slug = slug or _unique_slug(name)
    dest = _registry_entry_dir(final_slug)
    if os.path.exists(dest):
        raise ValueError(f"Model slug already exists: {final_slug}")
    os.makedirs(dest, exist_ok=True)
    for fname in MODEL_WEIGHT_FILES + MODEL_OPTIONAL_FILES:
        src = os.path.join(model_dir, fname)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(dest, fname))
    manifest_path = os.path.join(model_dir, "manifest.json")
    if os.path.isfile(manifest_path):
        manifest = _read_json(manifest_path) or {}
        manifest.setdefault("name", name)
        manifest["slug"] = final_slug
        manifest.setdefault("imported_at", _now_iso())
    else:
        manifest = _build_manifest(name, final_slug)
        manifest["imported_at"] = _now_iso()
    _write_json(os.path.join(dest, "manifest.json"), manifest)
    return manifest


def import_nbmodel_zip(data: bytes, name_override: Optional[str] = None) -> dict:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = zf.namelist()
        if "manifest.json" not in names:
            raise ValueError("Invalid .nbmodel.zip: missing manifest.json")
        manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
        display_name = name_override or manifest.get("name") or "imported-model"
        slug = _unique_slug(manifest.get("slug") or display_name)
        dest = _registry_entry_dir(slug)
        os.makedirs(dest, exist_ok=True)
        for fname in MODEL_WEIGHT_FILES + MODEL_OPTIONAL_FILES:
            if fname in names:
                zf.extract(fname, dest)
        manifest["name"] = display_name
        manifest["slug"] = slug
        manifest["imported_at"] = _now_iso()
        manifest.setdefault("format_version", NBMODEL_FORMAT_VERSION)
        _write_json(os.path.join(dest, "manifest.json"), manifest)
        _validate_model_dir(dest)
        return manifest


def export_nbmodel_zip(slug: Optional[str] = None, display_name: Optional[str] = None) -> tuple[bytes, str]:
    cleanup_tmp = False
    if slug:
        src_dir = _registry_entry_dir(slug)
        manifest = _read_manifest(slug)
        if not manifest:
            raise ValueError(f"Unknown model slug: {slug}")
    else:
        bootstrap_legacy_if_needed()
        _validate_model_dir(MODEL_DIR)
        tmp_slug = "_export_tmp"
        src_dir = _registry_entry_dir(tmp_slug)
        if os.path.isdir(src_dir):
            shutil.rmtree(src_dir)
        os.makedirs(src_dir, exist_ok=True)
        _copy_runtime_to_dir(src_dir)
        name = display_name or "current"
        manifest = _build_manifest(name, slugify(name))
        _write_json(os.path.join(src_dir, "manifest.json"), manifest)
        cleanup_tmp = True
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(os.path.join(src_dir, "manifest.json"), "manifest.json")
        for fname in MODEL_WEIGHT_FILES + MODEL_OPTIONAL_FILES:
            fpath = os.path.join(src_dir, fname)
            if os.path.isfile(fpath):
                zf.write(fpath, fname)
    filename = f"{slugify(manifest.get('slug') or manifest.get('name') or slug or 'model')}.nbmodel.zip"
    if cleanup_tmp and os.path.isdir(_registry_entry_dir("_export_tmp")):
        shutil.rmtree(_registry_entry_dir("_export_tmp"))
    return buf.getvalue(), filename


def export_current_model(name: str) -> tuple[bytes, str]:
    if not (name or "").strip():
        raise ValueError("name is required")
    return export_nbmodel_zip(slug=None, display_name=name.strip())


def create_new_model_from_runtime(name: str) -> dict:
    """Register fresh runtime weights as a new named model and mark it active."""
    display_name = (name or "").strip()
    if not display_name:
        raise ValueError("name is required")
    _validate_model_dir(MODEL_DIR)
    slug = _unique_slug(display_name)
    dest = _registry_entry_dir(slug)
    os.makedirs(dest, exist_ok=True)
    for fname in MODEL_WEIGHT_FILES + MODEL_OPTIONAL_FILES:
        src = os.path.join(MODEL_DIR, fname)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(dest, fname))
    manifest = _build_manifest(display_name, slug)
    manifest["imported_at"] = _now_iso()
    manifest["created_from_scratch"] = True
    _write_json(os.path.join(dest, "manifest.json"), manifest)
    activated = {
        "slug": slug,
        "name": display_name,
        "activated_at": _now_iso(),
    }
    _write_json(ACTIVE_MODEL_PATH, activated)
    return {**manifest, **activated, "active": True}


def activate_model(slug: str, reload_fn) -> dict:
    bootstrap_legacy_if_needed()
    src_dir = _registry_entry_dir(slug)
    if not os.path.isdir(src_dir):
        raise ValueError(f"Unknown model slug: {slug}")
    manifest = _read_manifest(slug)
    if not manifest:
        raise ValueError(f"Missing manifest for slug: {slug}")
    _validate_model_dir(src_dir)
    _copy_dir_to_runtime(src_dir)
    reload_fn()
    activated = {
        "slug": slug,
        "name": manifest.get("name") or slug,
        "activated_at": _now_iso(),
    }
    _write_json(ACTIVE_MODEL_PATH, activated)
    return {**manifest, **activated, "active": True}


def delete_model(slug: str) -> None:
    active = _read_json(ACTIVE_MODEL_PATH) or {}
    if active.get("slug") == slug:
        raise ValueError("Cannot delete the active model")
    dest = _registry_entry_dir(slug)
    if not os.path.isdir(dest):
        raise ValueError(f"Unknown model slug: {slug}")
    shutil.rmtree(dest)
