"""Windows sensor readout: LibreHardwareMonitorLib (via pythonnet) for CPU/GPU/
RAM/storage temp, shutil for storage capacity, and a Windows PDH performance
counter as an integrated-GPU load fallback when LHM doesn't enumerate a GPU.

Deliberately brand-agnostic: picks sensors by SensorType + fuzzy name matching
instead of hardcoding vendor-specific sensor name strings, so it doesn't need
to know in advance whether the CPU/GPU is Intel/AMD/NVIDIA.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys

import win32pdh

logger = logging.getLogger(__name__)

_LIBS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "libs")


def _load_lhm_types():
    if _LIBS_DIR not in sys.path:
        sys.path.append(_LIBS_DIR)
    import clr

    clr.AddReference("LibreHardwareMonitorLib")
    from LibreHardwareMonitor.Hardware import Computer, HardwareType, SensorType

    return Computer, HardwareType, SensorType


class IGpuPdhReader:
    """Sums '% 3D Utilization' across all GPU Engine instances. Fallback path
    for integrated GPUs LHM doesn't enumerate as GPU hardware."""

    def __init__(self):
        self.query = None
        self.counters: list = []
        self._primed = False

    def _open(self) -> bool:
        try:
            paths = win32pdh.ExpandWildCardPath(r"\GPU Engine(*engtype_3D)\Utilization Percentage")
        except Exception:
            logger.debug("GPU Engine PDH counter unavailable", exc_info=True)
            return False
        if not paths:
            return False
        self.query = win32pdh.OpenQuery()
        for p in paths:
            try:
                self.counters.append(win32pdh.AddCounter(self.query, p))
            except Exception:
                logger.debug("Failed to add GPU Engine counter %s", p, exc_info=True)
        return bool(self.counters)

    def read(self) -> float | None:
        if self.query is None and not self._open():
            return None
        try:
            win32pdh.CollectQueryData(self.query)
        except Exception:
            logger.debug("PDH CollectQueryData failed for GPU Engine counters", exc_info=True)
            return None
        if not self._primed:
            self._primed = True
            return None
        total = 0.0
        for c in self.counters:
            try:
                _, value = win32pdh.GetFormattedCounterValue(c, win32pdh.PDH_FMT_DOUBLE)
                total += value
            except Exception:
                continue
        return min(total, 100.0)

    def close(self) -> None:
        if self.query is not None:
            try:
                win32pdh.CloseQuery(self.query)
            except Exception:
                logger.debug("Error closing PDH query", exc_info=True)
            self.query = None


class SensorReader:
    def __init__(self):
        Computer, HardwareType, SensorType = _load_lhm_types()
        self._HardwareType = HardwareType
        self._SensorType = SensorType

        self.computer = Computer()
        self.computer.IsCpuEnabled = True
        self.computer.IsGpuEnabled = True
        self.computer.IsMemoryEnabled = True
        self.computer.IsStorageEnabled = True
        self.computer.Open()

        self._igpu_pdh = IGpuPdhReader()

    def close(self) -> None:
        try:
            self.computer.Close()
        except Exception:
            logger.debug("Error closing LHM Computer", exc_info=True)
        self._igpu_pdh.close()

    def _update_all(self, hardware) -> None:
        hardware.Update()
        for sub in hardware.SubHardware:
            self._update_all(sub)

    def _pick(self, sensors, sensor_type, prefer: list[str]) -> float | None:
        candidates = [s for s in sensors if s.SensorType == sensor_type and s.Value is not None]
        if not candidates:
            return None
        for p in prefer:
            for s in candidates:
                if p in str(s.Name).lower():
                    return float(s.Value)
        return float(candidates[0].Value)

    def read(self) -> dict:
        stats: dict = {}
        cpu_hw = None
        gpu_hw = None
        memory_hw = None
        storage_temp = None

        for hw in self.computer.Hardware:
            self._update_all(hw)
            if cpu_hw is None and hw.HardwareType == self._HardwareType.Cpu:
                cpu_hw = hw
            elif gpu_hw is None and hw.HardwareType in (
                self._HardwareType.GpuNvidia,
                self._HardwareType.GpuAmd,
                self._HardwareType.GpuIntel,
            ):
                gpu_hw = hw
            elif hw.HardwareType == self._HardwareType.Memory and "virtual" not in str(hw.Name).lower():
                if memory_hw is None:
                    memory_hw = hw
            elif storage_temp is None and hw.HardwareType == self._HardwareType.Storage:
                storage_temp = self._pick(list(hw.Sensors), self._SensorType.Temperature, ["temperature", "composite", "drive"])

        if cpu_hw is not None:
            sensors = list(cpu_hw.Sensors)
            stats["cpu_temp_c"] = self._pick(
                sensors, self._SensorType.Temperature, ["package", "tctl", "die", "average"]
            )
            stats["cpu_load_pct"] = self._pick(sensors, self._SensorType.Load, ["total"])
            clock = self._pick(sensors, self._SensorType.Clock, ["average"])
            stats["cpu_freq_ghz"] = (clock / 1000.0) if clock else None

        if gpu_hw is not None:
            sensors = list(gpu_hw.Sensors)
            stats["gpu_temp_c"] = self._pick(
                sensors, self._SensorType.Temperature, ["core", "hot spot", "edge"]
            )
            stats["gpu_load_pct"] = self._pick(sensors, self._SensorType.Load, ["core", "3d", "total"])
        else:
            igpu_load = self._igpu_pdh.read()
            if igpu_load is not None:
                stats["gpu_load_pct"] = igpu_load

        if memory_hw is not None:
            sensors = list(memory_hw.Sensors)
            stats["ram_used_pct"] = self._pick(sensors, self._SensorType.Load, ["memory"])
            ram_used = self._pick(sensors, self._SensorType.Data, ["used"])
            ram_available = self._pick(sensors, self._SensorType.Data, ["available"])
            if ram_used is not None:
                stats["ram_used_gb"] = ram_used
            if ram_used is not None and ram_available is not None:
                stats["ram_total_gb"] = ram_used + ram_available

        if storage_temp is not None:
            stats["ssd_temp_c"] = storage_temp

        try:
            usage = shutil.disk_usage("C:\\")
            stats["ssd_used_gb"] = usage.used / (1024 ** 3)
            stats["ssd_total_gb"] = usage.total / (1024 ** 3)
        except OSError:
            logger.debug("Failed to read C: disk usage", exc_info=True)

        return stats
