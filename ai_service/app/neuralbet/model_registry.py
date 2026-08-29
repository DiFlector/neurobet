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

from typing import Any, Optional

from app.config import (
    ACTIVE_MODEL_PATH,
    ACTIVE_MODELS_PATH,
    DEPLOY_MODE,
    IS_DEV,
    MODEL_DIR,
    REGISTRY_DIR,
    SLOT2_RUNTIME_DIR,
)

MODEL_WEIGHT_FILES = ("pytorch_gru.pt", "lightgbm_model.txt")
MODEL_OPTIONAL_FILES = ("lightgbm_meta.json",)
NBMODEL_FORMAT_VERSION = 1
NBMODEL_GROUP_FORMAT_VERSION = 1

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


def _read_active_models_config() -> dict:
    data = _read_json(ACTIVE_MODELS_PATH)
    if data and isinstance(data.get("slots"), list) and data["slots"]:
        return data
    legacy = _read_json(ACTIVE_MODEL_PATH)
    if legacy and legacy.get("slug"):
        return {
            "group_name": None,
            "slots": [{
                "slot": 1,
                "slug": legacy["slug"],
                "name": legacy.get("name") or legacy["slug"],
                "activated_at": legacy.get("activated_at"),
            }],
        }
    return {"group_name": None, "slots": []}


def _write_active_models_config(config: dict) -> None:
    slots = config.get("slots") or []
    payload = {
        "group_name": config.get("group_name"),
        "slots": slots,
    }
    _write_json(ACTIVE_MODELS_PATH, payload)
    if slots:
        primary = next((s for s in slots if int(s.get("slot") or 0) == 1), slots[0])
        _write_json(ACTIVE_MODEL_PATH, {
            "slug": primary.get("slug"),
            "name": primary.get("name") or primary.get("slug"),
            "activated_at": primary.get("activated_at"),
        })
    elif os.path.exists(ACTIVE_MODEL_PATH):
        try:
            os.remove(ACTIVE_MODEL_PATH)
        except OSError:
            pass


def _slot_entry(config: dict, slot: int) -> Optional[dict]:
    for entry in config.get("slots") or []:
        if int(entry.get("slot") or 0) == slot:
            return entry
    return None


def _active_slugs(config: dict) -> set[str]:
    return {str(s.get("slug")) for s in (config.get("slots") or []) if s.get("slug")}


def get_active_models() -> dict:
    """Full active-model config: group_name + up to two slots."""
    bootstrap_legacy_if_needed()
    config = _read_active_models_config()
    enriched_slots: list[dict] = []
    for entry in config.get("slots") or []:
        slug = str(entry.get("slug") or "")
        manifest = _read_manifest(slug) if slug else None
        enriched_slots.append({
            **(manifest or {}),
            "slot": int(entry.get("slot") or 1),
            "slug": slug,
            "name": entry.get("name") or (manifest or {}).get("name") or slug,
            "activated_at": entry.get("activated_at"),
            "active": True,
        })
    return {
        "group_name": config.get("group_name"),
        "slots": enriched_slots,
        "dual_active": len(enriched_slots) >= 2,
    }


def get_active_model() -> Optional[dict]:
    """Primary (slot 1) active model — backward compatible."""
    active = get_active_models()
    slots = active.get("slots") or []
    if not slots:
        return None
    primary = next((s for s in slots if int(s.get("slot") or 0) == 1), slots[0])
    return {**primary, "active": True}


def get_public_active_model() -> Optional[dict]:
    """Model name for the public neurobets page — registry entry or dev runtime fallback."""
    bootstrap_legacy_if_needed()
    active = get_active_models()
    slots = active.get("slots") or []
    if slots:
        group_name = (active.get("group_name") or "").strip()
        if len(slots) >= 2 and group_name:
            return {
                "name": group_name,
                "slug": "+".join(str(s.get("slug") or "") for s in slots),
                "runtime": False,
                "dual_active": True,
                "slots": [
                    {"slot": s.get("slot"), "name": s.get("name"), "slug": s.get("slug")}
                    for s in slots
                ],
            }
        primary = next((s for s in slots if int(s.get("slot") or 0) == 1), slots[0])
        return {
            "name": primary.get("name") or primary.get("slug"),
            "slug": primary.get("slug"),
            "runtime": False,
            "dual_active": len(slots) >= 2,
            "slots": [
                {"slot": s.get("slot"), "name": s.get("name"), "slug": s.get("slug")}
                for s in slots
            ] if len(slots) >= 2 else None,
        }

    if os.path.isfile(PYTORCH_WEIGHTS_PATH):
        bootstrap_legacy_if_needed()
        active = get_active_models()
        slots = active.get("slots") or []
        if slots:
            primary = next((s for s in slots if int(s.get("slot") or 0) == 1), slots[0])
            return {
                "name": primary.get("name") or primary.get("slug"),
                "slug": primary.get("slug"),
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
    active_slugs = _active_slugs(_read_active_models_config())
    config = _read_active_models_config()
    slot_by_slug = {
        str(s.get("slug")): int(s.get("slot") or 1)
        for s in (config.get("slots") or [])
        if s.get("slug")
    }
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
        slug = manifest.get("slug") or name
        active_slot = slot_by_slug.get(slug)
        out.append({
            **manifest,
            "slug": slug,
            "active": slug in active_slugs,
            "active_slot": active_slot,
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
    if not os.path.exists(ACTIVE_MODELS_PATH) and not os.path.exists(ACTIVE_MODEL_PATH):
        _write_active_models_config({
            "group_name": None,
            "slots": [{
                "slot": 1,
                "slug": slug,
                "name": manifest["name"],
                "activated_at": _now_iso(),
            }],
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
        "slot": 1,
        "slug": slug,
        "name": display_name,
        "activated_at": _now_iso(),
    }
    _write_active_models_config({"group_name": None, "slots": [activated]})
    return {**manifest, **activated, "active": True}


def _copy_dir_to_slot_runtime(src_dir: str, slot: int) -> None:
    dest_dir = MODEL_DIR if slot == 1 else SLOT2_RUNTIME_DIR
    os.makedirs(dest_dir, exist_ok=True)
    for fname in MODEL_WEIGHT_FILES + MODEL_OPTIONAL_FILES:
        src = os.path.join(src_dir, fname)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(dest_dir, fname))


def _clear_slot_runtime(slot: int) -> None:
    if slot != 2:
        return
    if os.path.isdir(SLOT2_RUNTIME_DIR):
        shutil.rmtree(SLOT2_RUNTIME_DIR)


def set_group_name(group_name: Optional[str]) -> dict:
    config = _read_active_models_config()
    slots = config.get("slots") or []
    if len(slots) < 2:
        raise ValueError("Group name applies only when two models are active")
    config["group_name"] = (group_name or "").strip() or None
    _write_active_models_config(config)
    return get_active_models()


def deactivate_slot(slot: int, reload_fn) -> dict:
    if slot != 2:
        raise ValueError("Only slot 2 can be deactivated independently")
    config = _read_active_models_config()
    slots = [s for s in (config.get("slots") or []) if int(s.get("slot") or 0) != 2]
    if len(slots) == len(config.get("slots") or []):
        raise ValueError("Slot 2 is not active")
    config["slots"] = slots
    config["group_name"] = None
    _write_active_models_config(config)
    _clear_slot_runtime(2)
    reload_fn()
    return get_active_models()


def activate_model(slug: str, reload_fn, slot: int = 1) -> dict:
    if slot not in (1, 2):
        raise ValueError("slot must be 1 or 2")
    bootstrap_legacy_if_needed()
    src_dir = _registry_entry_dir(slug)
    if not os.path.isdir(src_dir):
        raise ValueError(f"Unknown model slug: {slug}")
    manifest = _read_manifest(slug)
    if not manifest:
        raise ValueError(f"Missing manifest for slug: {slug}")
    _validate_model_dir(src_dir)

    config = _read_active_models_config()
    slots = list(config.get("slots") or [])
    if slot == 2 and not _slot_entry(config, 1):
        raise ValueError("Activate slot 1 before slot 2")

    _copy_dir_to_slot_runtime(src_dir, slot)
    activated = {
        "slot": slot,
        "slug": slug,
        "name": manifest.get("name") or slug,
        "activated_at": _now_iso(),
    }
    slots = [s for s in slots if int(s.get("slot") or 0) != slot]
    slots.append(activated)
    slots.sort(key=lambda s: int(s.get("slot") or 0))
    config["slots"] = slots
    if len(slots) < 2:
        config["group_name"] = None
    _write_active_models_config(config)
    reload_fn()
    return {**manifest, **activated, "active": True}


def delete_model(slug: str) -> None:
    active_slugs = _active_slugs(_read_active_models_config())
    if slug in active_slugs:
        raise ValueError("Cannot delete an active model — deactivate it first")
    dest = _registry_entry_dir(slug)
    if not os.path.isdir(dest):
        raise ValueError(f"Unknown model slug: {slug}")
    shutil.rmtree(dest)


def export_model_group() -> tuple[bytes, str]:
    active = get_active_models()
    slots = active.get("slots") or []
    if len(slots) < 2:
        raise ValueError("Two active models required for group export")
    group_name = (active.get("group_name") or "").strip()
    if not group_name:
        raise ValueError("Set a group name before exporting")

    buf = io.BytesIO()
    manifest_models: list[dict] = []
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for entry in sorted(slots, key=lambda s: int(s.get("slot") or 0)):
            slot = int(entry.get("slot") or 0)
            slug = str(entry.get("slug") or "")
            src_dir = _registry_entry_dir(slug)
            if not os.path.isdir(src_dir):
                raise ValueError(f"Unknown model slug: {slug}")
            _validate_model_dir(src_dir)
            prefix = f"slot{slot}/"
            model_manifest = _read_manifest(slug) or {}
            manifest_models.append({
                "slot": slot,
                "name": entry.get("name") or model_manifest.get("name") or slug,
                "slug": slug,
            })
            zf.write(os.path.join(src_dir, "manifest.json"), f"{prefix}manifest.json")
            for fname in MODEL_WEIGHT_FILES + MODEL_OPTIONAL_FILES:
                fpath = os.path.join(src_dir, fname)
                if os.path.isfile(fpath):
                    zf.write(fpath, f"{prefix}{fname}")
        group_manifest = {
            "format_version": NBMODEL_GROUP_FORMAT_VERSION,
            "group_name": group_name,
            "exported_at": _now_iso(),
            "models": manifest_models,
        }
        zf.writestr("manifest.json", json.dumps(group_manifest, ensure_ascii=False, indent=2))
    filename = f"{slugify(group_name)}.nbmodelgroup.zip"
    return buf.getvalue(), filename


def import_model_group(data: bytes) -> dict:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = zf.namelist()
        if "manifest.json" not in names:
            raise ValueError("Invalid .nbmodelgroup.zip: missing manifest.json")
        group_manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
        if int(group_manifest.get("format_version") or 0) != NBMODEL_GROUP_FORMAT_VERSION:
            raise ValueError("Unsupported group format version")
        group_name = (group_manifest.get("group_name") or "").strip()
        if not group_name:
            raise ValueError("Group manifest missing group_name")
        models = group_manifest.get("models") or []
        if len(models) != 2:
            raise ValueError("Group must contain exactly two models")

        imported: list[dict] = []
        for model_entry in sorted(models, key=lambda m: int(m.get("slot") or 0)):
            slot = int(model_entry.get("slot") or 0)
            if slot not in (1, 2):
                raise ValueError(f"Invalid slot in group: {slot}")
            prefix = f"slot{slot}/"
            if f"{prefix}manifest.json" not in names:
                raise ValueError(f"Missing {prefix}manifest.json in group archive")
            tmp_dir = _registry_entry_dir(f"_import_tmp_slot{slot}")
            if os.path.isdir(tmp_dir):
                shutil.rmtree(tmp_dir)
            os.makedirs(tmp_dir, exist_ok=True)
            for fname in ("manifest.json",) + MODEL_WEIGHT_FILES + MODEL_OPTIONAL_FILES:
                arcname = f"{prefix}{fname}"
                if arcname in names:
                    with open(os.path.join(tmp_dir, fname), "wb") as out_f:
                        out_f.write(zf.read(arcname))
            display_name = model_entry.get("name") or f"slot-{slot}"
            manifest = register_from_dir(tmp_dir, display_name)
            shutil.rmtree(tmp_dir, ignore_errors=True)
            imported.append({**manifest, "slot": slot})

    activated_slots: list[dict] = []
    for entry in imported:
        slot = int(entry["slot"])
        slug = str(entry["slug"])
        src_dir = _registry_entry_dir(slug)
        _copy_dir_to_slot_runtime(src_dir, slot)
        activated_slots.append({
            "slot": slot,
            "slug": slug,
            "name": entry.get("name") or slug,
            "activated_at": _now_iso(),
        })

    _write_active_models_config({
        "group_name": group_name,
        "slots": activated_slots,
    })
    return {
        "group_name": group_name,
        "slots": activated_slots,
        "imported": imported,
    }
