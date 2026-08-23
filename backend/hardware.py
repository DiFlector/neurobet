"""Host hardware snapshot for the admin panel (CPU / RAM / disk / GPU).

Reads /proc via psutil — inside Docker that is the host kernel's view of CPU
and RAM, which is what you want while training saturates the box. Disk is the
filesystem that holds /app/data (models + postgres bind-mount parent). GPU
comes from nvidia-smi when the driver is visible; otherwise we ask ai_service
(torch.cuda) so a CUDA worker still shows up.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
import time
from typing import Any, Optional

logger = logging.getLogger("hardware")

DATA_PATH = os.getenv("NEUROBET_DATA_PATH", "/app/data")
_SAMPLE_SECONDS = float(os.getenv("NEUROBET_HW_SAMPLE_SECONDS", "2"))

_gpu_cache: dict[str, Any] = {"loaded_at": 0.0, "data": None}
_GPU_CACHE_SECONDS = 8.0
_lock = threading.Lock()
_cpu_percent: Optional[float] = None
_cpu_per_core: list[float] = []
_sampler_started = False


def _load_avg() -> Optional[list[float]]:
    try:
        return [round(x, 2) for x in os.getloadavg()]
    except (OSError, AttributeError):
        try:
            import psutil
            return [round(x, 2) for x in psutil.getloadavg()]
        except Exception:
            return None


def _cpu_loop() -> None:
    global _cpu_percent, _cpu_per_core
    import psutil

    psutil.cpu_percent(interval=None)
    psutil.cpu_percent(interval=None, percpu=True)
    while True:
        time.sleep(max(_SAMPLE_SECONDS, 0.5))
        overall = psutil.cpu_percent(interval=None)
        per_core = psutil.cpu_percent(interval=None, percpu=True)
        with _lock:
            _cpu_percent = overall
            _cpu_per_core = per_core


def start_hardware_sampler() -> None:
    global _sampler_started
    if _sampler_started:
        return
    _sampler_started = True
    threading.Thread(target=_cpu_loop, daemon=True, name="hw-cpu-sampler").start()


def _bytes_info(used: int, total: int, percent: Optional[float] = None) -> dict[str, Any]:
    total = max(int(total or 0), 0)
    used = max(int(used or 0), 0)
    if percent is None:
        percent = round((used / total) * 100.0, 1) if total else 0.0
    else:
        percent = round(float(percent), 1)
    return {
        "used_bytes": used,
        "total_bytes": total,
        "free_bytes": max(total - used, 0),
        "percent": percent,
    }


def _disk_snapshot() -> dict[str, Any]:
    path = DATA_PATH if os.path.isdir(DATA_PATH) else os.sep
    try:
        usage = shutil.disk_usage(path)
        info = _bytes_info(usage.used, usage.total)
        info["path"] = path
        return info
    except OSError as e:
        logger.warning(f"disk_usage({path}) failed: {e}")
        return {"used_bytes": 0, "total_bytes": 0, "free_bytes": 0, "percent": 0.0, "path": path}


def _nvidia_smi_gpus() -> Optional[list[dict[str, Any]]]:
    try:
        proc = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=1.5,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0 or not (proc.stdout or "").strip():
        return None
    gpus: list[dict[str, Any]] = []
    for line in proc.stdout.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 5:
            continue
        try:
            mem_used = int(float(parts[2])) * 1024 * 1024
            mem_total = int(float(parts[3])) * 1024 * 1024
            util = float(parts[1])
            temp_raw = parts[4]
            temp = None if temp_raw in ("[N/A]", "N/A", "") else float(temp_raw)
        except (TypeError, ValueError):
            continue
        mem = _bytes_info(mem_used, mem_total)
        gpus.append({
            "name": parts[0],
            "util_percent": round(util, 1),
            "memory": mem,
            "temperature_c": temp,
        })
    return gpus or None


def _ai_gpu_snapshot(ai_url: Optional[str]) -> Optional[dict[str, Any]]:
    if not ai_url:
        return None
    try:
        import httpx
        with httpx.Client(timeout=1.5) as client:
            res = client.get(f"{ai_url.rstrip('/')}/hardware")
        if res.status_code != 200:
            return None
        data = res.json()
        gpu = data.get("gpu") if isinstance(data, dict) else None
        return gpu if isinstance(gpu, dict) else None
    except Exception:
        return None


def _gpu_snapshot(ai_url: Optional[str] = None) -> dict[str, Any]:
    now = time.time()
    cached = _gpu_cache.get("data")
    if cached is not None and now - float(_gpu_cache.get("loaded_at") or 0) < _GPU_CACHE_SECONDS:
        return cached

    local = _nvidia_smi_gpus()
    if local:
        result = {"available": True, "source": "nvidia-smi", "gpus": local}
    else:
        from_ai = _ai_gpu_snapshot(ai_url)
        if from_ai and from_ai.get("available") and from_ai.get("gpus"):
            from_ai.setdefault("source", "ai_service")
            result = from_ai
        else:
            reason = "NVIDIA драйвер не виден из контейнера — обучение идёт на CPU"
            if from_ai and from_ai.get("reason"):
                reason = str(from_ai["reason"])
            result = {"available": False, "source": None, "gpus": [], "reason": reason}

    _gpu_cache["loaded_at"] = now
    _gpu_cache["data"] = result
    return result


def get_hardware_snapshot(*, ai_url: Optional[str] = None) -> dict[str, Any]:
    import psutil

    start_hardware_sampler()
    with _lock:
        cpu_pct = _cpu_percent
        per_core = list(_cpu_per_core)

    if cpu_pct is None:
        cpu_pct = psutil.cpu_percent(interval=0.12)
        per_core = psutil.cpu_percent(interval=None, percpu=True)

    vm = psutil.virtual_memory()
    cores_logical = psutil.cpu_count(logical=True) or 0
    cores_physical = psutil.cpu_count(logical=False) or cores_logical

    return {
        "cpu": {
            "percent": round(float(cpu_pct or 0.0), 1),
            "load_avg": _load_avg(),
            "cores_logical": cores_logical,
            "cores_physical": cores_physical,
            "per_core": [round(x, 1) for x in per_core],
        },
        "memory": _bytes_info(int(vm.used), int(vm.total), percent=float(vm.percent)),
        "disk": _disk_snapshot(),
        "gpu": _gpu_snapshot(ai_url),
        "sampled_at": time.time(),
    }
