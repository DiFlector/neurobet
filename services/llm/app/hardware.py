"""Hardware detection and GPU/CPU layer offloading manager."""

import logging
import os
import shutil
import subprocess
from typing import Dict, Any, Tuple

logger = logging.getLogger("llm.hardware")


class HardwareManager:
    """Manages GPU offloading and CPU fallbacks for local LLM inference."""

    @staticmethod
    def detect_nvidia_gpu() -> Tuple[bool, Dict[str, Any]]:
        """
        Detects NVIDIA GPU presence and memory via nvidia-smi or torch/cuda.
        Returns (is_available, details_dict).
        """
        details: Dict[str, Any] = {
            "gpu_detected": False,
            "gpu_name": None,
            "total_vram_mb": 0,
            "free_vram_mb": 0,
            "driver_version": None,
        }

        # 1. Check if nvidia-smi is available
        nvidia_smi = shutil.which("nvidia-smi")
        if not nvidia_smi:
            logger.info("nvidia-smi not found in PATH. Using CPU fallback.")
            return False, details

        try:
            cmd = [
                nvidia_smi,
                "--query-gpu=name,memory.total,memory.free,driver_version",
                "--format=csv,noheader,nounits",
            ]
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5)
            if proc.returncode == 0 and proc.stdout.strip():
                line = proc.stdout.strip().split("\n")[0]
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 4:
                    details["gpu_detected"] = True
                    details["gpu_name"] = parts[0]
                    details["total_vram_mb"] = int(float(parts[1]))
                    details["free_vram_mb"] = int(float(parts[2]))
                    details["driver_version"] = parts[3]
                    logger.info(
                        f"Detected GPU: {details['gpu_name']} ({details['total_vram_mb']}MB VRAM, "
                        f"{details['free_vram_mb']}MB free)"
                    )
                    return True, details
        except Exception as e:
            logger.warning(f"Error querying nvidia-smi: {e}. Falling back to CPU.")

        return False, details

    @classmethod
    def calculate_gpu_layers(cls, requested_layers: str) -> Tuple[int, str]:
        """
        Calculates number of layers to offload to GPU.
        Returns (n_gpu_layers, device_mode).
        """
        has_gpu, gpu_info = cls.detect_nvidia_gpu()

        if not has_gpu:
            return 0, "cpu"

        # Explicit CPU request
        if requested_layers in ("0", "cpu", "none"):
            return 0, "cpu"

        # If user explicitly passed an integer
        try:
            layers_int = int(requested_layers)
            if layers_int >= 0:
                return layers_int, "cuda"
        except ValueError:
            pass

        # Auto configuration:
        # GTX 1050 Ti has ~4096 MB VRAM.
        # A 3B Q4 model has ~36 layers and occupies ~1800 MB VRAM.
        # We can safely offload ~28-32 layers or all (-1).
        free_mb = gpu_info.get("free_vram_mb", 0)
        if free_mb >= 3000:
            # Full offload
            return 32, "cuda"
        elif free_mb >= 1500:
            # Partial offload
            return 20, "cuda"
        else:
            logger.warning(f"Insufficient VRAM ({free_mb}MB free). Falling back to CPU.")
            return 0, "cpu"
