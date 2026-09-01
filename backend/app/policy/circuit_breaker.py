class CircuitBreaker:
    """Stopping rule: once the false-intervention rate in the current batch
    exceeds the configured threshold, halts all auto-actions and stays tripped
    for the rest of the batch. Requires a minimum sample size before evaluating
    the rate, so a single early false intervention cannot trip it on its own."""

    def __init__(self, threshold: float, min_sample: int = 20):
        self._threshold = threshold
        self._min_sample = min_sample
        self._total = 0
        self._false = 0
        self._tripped = False

    def record(self, was_false_intervention: bool) -> bool:
        """Records one intervention outcome. Returns True if this call is the
        one that newly tripped the breaker (for one-time halt logging)."""
        if self._tripped:
            return False

        self._total += 1
        if was_false_intervention:
            self._false += 1

        if self._total >= self._min_sample and (self._false / self._total) > self._threshold:
            self._tripped = True
            return True
        return False

    @property
    def tripped(self) -> bool:
        return self._tripped

    @property
    def false_intervention_rate(self) -> float:
        return self._false / self._total if self._total else 0.0
