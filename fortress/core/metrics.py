"""Metrics collection for Fortress daemon."""

import time
from collections import deque


class Metrics:
    """Collect and report daemon metrics."""

    def __init__(self):
        self.events_count = 0
        self.actions_count = 0
        self.llm_calls = 0
        self.errors = 0
        self.rules_matched = 0
        self.llm_decisions = 0
        self._latencies: deque[float] = deque(maxlen=100)
        self._start_time = time.monotonic()

    def record_event(self) -> None:
        self.events_count += 1

    def record_action(self) -> None:
        self.actions_count += 1

    def record_llm_call(self, latency_ms: float) -> None:
        self.llm_calls += 1
        self._latencies.append(latency_ms)

    def record_rule_match(self) -> None:
        self.rules_matched += 1

    def record_llm_decision(self) -> None:
        self.llm_decisions += 1

    def record_error(self) -> None:
        self.errors += 1

    def uptime(self) -> float:
        return time.monotonic() - self._start_time

    def avg_latency_ms(self) -> float:
        if not self._latencies:
            return 0.0
        return sum(self._latencies) / len(self._latencies)

    def rules_pct(self) -> float:
        if self.events_count == 0:
            return 0.0
        return self.rules_matched / self.events_count * 100

    def snapshot(self) -> dict:
        return {
            "uptime_seconds": round(self.uptime(), 1),
            "events": self.events_count,
            "actions": self.actions_count,
            "errors": self.errors,
            "llm_calls": self.llm_calls,
            "llm_decisions": self.llm_decisions,
            "rules_matched": self.rules_matched,
            "rules_pct": round(self.rules_pct(), 1),
            "avg_latency_ms": round(self.avg_latency_ms(), 1),
        }
