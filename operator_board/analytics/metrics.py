"""Time-series store for live instance metrics (from listener sync status, multi-user, etc.)."""
from __future__ import annotations

import csv
import threading
import time
from collections import defaultdict, deque
from typing import Any, Deque, Dict, Iterable, List, Optional, Tuple

Sample = Tuple[float, float]

# listener field -> friendly metric name. GPU fields are per-GPU lists.
KNOWN_FIELDS = {
    "cpuUtilization": "CPU %",
    "gpuUtilization": "GPU %",
    "gpuCoreClocksKhz": "GPU clock (MHz)",
    "gpuTemperature": "GPU temp (C)",
    "availablePhysicalMemory": "Free RAM (GB)",
    "totalPhysicalMemory": "Total RAM (GB)",
}


def _mean(values: Iterable[float]) -> Optional[float]:
    vals = [float(v) for v in values if isinstance(v, (int, float))]
    return sum(vals) / len(vals) if vals else None


def normalise_metrics(raw: Dict[str, Any]) -> Dict[str, float]:
    """Convert a listener sync-status payload into flat named numbers."""
    out: Dict[str, float] = {}
    for key, label in KNOWN_FIELDS.items():
        if key not in raw:
            continue
        value = raw[key]
        if isinstance(value, list):
            value = _mean(value)
        if not isinstance(value, (int, float)):
            continue
        value = float(value)
        if key == "gpuCoreClocksKhz":
            value /= 1000.0
        elif key.endswith("PhysicalMemory") and value > 1024 ** 2:
            value /= 1024 ** 3
        out[label] = round(value, 2)
    for key, value in raw.items():
        if key not in KNOWN_FIELDS and isinstance(value, (int, float)) and not isinstance(value, bool):
            out[key] = float(value)
    return out


class MetricStore:
    def __init__(self, max_samples: int = 1800) -> None:
        self._lock = threading.Lock()
        self._series: Dict[Tuple[str, str], Deque[Sample]] = defaultdict(lambda: deque(maxlen=max_samples))
        self.raw: Dict[str, Dict[str, Any]] = {}

    def add(self, source: str, metric: str, value: float, t: Optional[float] = None) -> None:
        with self._lock:
            self._series[(source, metric)].append((t or time.time(), float(value)))

    def add_raw(self, source: str, raw: Dict[str, Any]) -> None:
        self.raw[source] = raw
        now = time.time()
        for metric, value in normalise_metrics(raw).items():
            self.add(source, metric, value, now)

    def sources(self) -> List[str]:
        with self._lock:
            return sorted({s for s, _ in self._series})

    def metrics(self, source: Optional[str] = None) -> List[str]:
        with self._lock:
            return sorted({m for s, m in self._series if source is None or s == source})

    def series(self, source: str, metric: str) -> List[Sample]:
        with self._lock:
            return list(self._series.get((source, metric), ()))

    def latest(self, source: str, metric: str) -> Optional[float]:
        s = self.series(source, metric)
        return s[-1][1] if s else None

    def export_csv(self, path: str) -> int:
        rows = 0
        with self._lock, open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["timestamp", "source", "metric", "value"])
            for (source, metric), samples in sorted(self._series.items()):
                for t, v in samples:
                    w.writerow([f"{t:.3f}", source, metric, v])
                    rows += 1
        return rows
