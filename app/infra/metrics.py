"""Minimal in-process metrics, exposed in Prometheus text format.

Deliberately dependency-free: this service has a handful of counters and gauges,
and the Prometheus client library would be more machinery than the job needs.
"""

from __future__ import annotations

import threading
from collections import defaultdict
from typing import Any


class Metrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[tuple, float] = defaultdict(float)
        self._gauges: dict[tuple, float] = {}

    @staticmethod
    def _labels(labels: dict[str, Any] | None) -> tuple:
        return tuple(sorted((k, str(v)) for k, v in (labels or {}).items()))

    def incr(self, name: str, value: float = 1.0, **labels: Any) -> None:
        with self._lock:
            self._counters[(name, self._labels(labels))] += value

    def gauge(self, name: str, value: float, **labels: Any) -> None:
        with self._lock:
            self._gauges[(name, self._labels(labels))] = value

    def render(self) -> str:
        """Prometheus exposition format."""
        lines: list[str] = []
        with self._lock:
            for kind, source in (("counter", self._counters), ("gauge", self._gauges)):
                declared: set[str] = set()
                for (name, labels), value in sorted(source.items()):
                    if name not in declared:
                        lines.append(f"# TYPE {name} {kind}")
                        declared.add(name)
                    rendered = ",".join('{}="{}"'.format(k, v) for k, v in labels)
                    suffix = "{" + rendered + "}" if rendered else ""
                    lines.append(f"{name}{suffix} {value}")
        return "\n".join(lines) + "\n"

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "counters": {
                    name + (str(dict(labels)) if labels else ""): value
                    for (name, labels), value in self._counters.items()
                },
                "gauges": {
                    name + (str(dict(labels)) if labels else ""): value
                    for (name, labels), value in self._gauges.items()
                },
            }

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()
            self._gauges.clear()


metrics = Metrics()
