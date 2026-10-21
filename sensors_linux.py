"""Linux sensor readout: psutil + hwmon (via psutil.sensors_temperatures) for
CPU/storage temp, CPU load and RAM, shutil for storage capacity, NVML for
NVIDIA GPUs, and amdgpu sysfs as a fallback for AMD (incl. integrated) GPUs.

Mirrors sensors_windows.SensorReader's output dict so render.py doesn't care
which OS it's on. Everything here is readable without root.
"""

from __future__ import annotations

import glob
import logging
import shutil

import psutil

logger = logging.getLogger(__name__)

# hwmon driver names, in preference order
_CPU_CHIPS = ["k10temp", "zenpower", "coretemp", "cpu_thermal"]
_CPU_LABELS = ["tctl", "tdie", "package", "die"]
_STORAGE_CHIPS = ["nvme", "drivetemp"]
_STORAGE_LABELS = ["composite"]


def _pick_temp(temps: dict, chips: list[str], prefer: list[str]) -> float | None:
    for chip in chips:
        entries = [e for e in temps.get(chip, []) if e.current is not None]
        if not entries:
            continue
        for p in prefer:
            for e in entries:
                if p in e.label.lower():
                    return float(e.current)
        return float(entries[0].current)
    return None


class NvidiaReader:
    """First NVIDIA GPU via NVML (nvidia-ml-py). Unavailable -> read() returns None."""

    def __init__(self):
        self._nvml = None
        self._handle = None
        try:
            import pynvml

            pynvml.nvmlInit()
            if pynvml.nvmlDeviceGetCount() > 0:
                self._nvml = pynvml
                self._handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        except Exception:
            logger.debug("NVML unavailable", exc_info=True)

    def read(self) -> tuple[float | None, float | None] | None:
        if self._handle is None:
            return None
        nvml = self._nvml
        try:
            temp = nvml.nvmlDeviceGetTemperature(self._handle, nvml.NVML_TEMPERATURE_GPU)
            load = nvml.nvmlDeviceGetUtilizationRates(self._handle).gpu
            return float(temp), float(load)
        except Exception:
            logger.debug("NVML read failed", exc_info=True)
            return None

    def close(self) -> None:
        if self._nvml is not None:
            try:
                self._nvml.nvmlShutdown()
            except Exception:
                logger.debug("Error shutting down NVML", exc_info=True)
            self._nvml = None
            self._handle = None


class AmdGpuReader:
    """amdgpu via sysfs: gpu_busy_percent for load, the 'amdgpu' hwmon for temp."""

    def __init__(self):
        busy = sorted(glob.glob("/sys/class/drm/card*/device/gpu_busy_percent"))
        self._busy_path = busy[0] if busy else None

    def read(self, temps: dict) -> tuple[float | None, float | None] | None:
        if self._busy_path is None:
            return None
        load = None
        try:
            with open(self._busy_path) as f:
                load = float(f.read().strip())
        except (OSError, ValueError):
            logger.debug("Failed to read %s", self._busy_path, exc_info=True)
        temp = _pick_temp(temps, ["amdgpu"], ["edge", "junction"])
        return temp, load


class SensorReader:
    def __init__(self):
        self._nvidia = NvidiaReader()
        self._amdgpu = AmdGpuReader()
        # Prime cpu_percent so the first real read() returns a meaningful delta
        psutil.cpu_percent(interval=None)

    def close(self) -> None:
        self._nvidia.close()

    def read(self) -> dict:
        stats: dict = {}

        try:
            temps = psutil.sensors_temperatures()
        except Exception:
            logger.debug("sensors_temperatures failed", exc_info=True)
            temps = {}

        stats["cpu_temp_c"] = _pick_temp(temps, _CPU_CHIPS, _CPU_LABELS)
        stats["cpu_load_pct"] = psutil.cpu_percent(interval=None)
        freq = psutil.cpu_freq()
        stats["cpu_freq_ghz"] = (freq.current / 1000.0) if freq else None

        gpu = self._nvidia.read() or self._amdgpu.read(temps)
        if gpu is not None:
            stats["gpu_temp_c"], stats["gpu_load_pct"] = gpu

        mem = psutil.virtual_memory()
        stats["ram_used_gb"] = (mem.total - mem.available) / (1024 ** 3)
        stats["ram_total_gb"] = mem.total / (1024 ** 3)
        stats["ram_used_pct"] = mem.percent

        ssd_temp = _pick_temp(temps, _STORAGE_CHIPS, _STORAGE_LABELS)
        if ssd_temp is not None:
            stats["ssd_temp_c"] = ssd_temp

        try:
            usage = shutil.disk_usage("/")
            stats["ssd_used_gb"] = usage.used / (1024 ** 3)
            stats["ssd_total_gb"] = usage.total / (1024 ** 3)
        except OSError:
            logger.debug("Failed to read / disk usage", exc_info=True)

        return stats
