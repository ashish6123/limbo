from app.policy.circuit_breaker import CircuitBreaker


def test_stays_closed_below_min_sample_even_at_100_percent_false():
    breaker = CircuitBreaker(threshold=0.02, min_sample=20)

    for _ in range(19):
        tripped_now = breaker.record(was_false_intervention=True)
        assert tripped_now is False

    assert breaker.tripped is False


def test_trips_once_rate_exceeds_threshold_past_min_sample():
    breaker = CircuitBreaker(threshold=0.02, min_sample=20)

    tripped_now = None
    for _ in range(20):
        tripped_now = breaker.record(was_false_intervention=True)

    assert breaker.tripped is True
    assert tripped_now is True


def test_stays_closed_when_rate_is_within_threshold():
    breaker = CircuitBreaker(threshold=0.02, min_sample=20)

    # Rate must stay under 2% at every sample size the breaker checks, not just
    # at the end: 60 clean interventions first, so the rate never spikes past
    # the min_sample checkpoint, then 1 false brings the running rate to 1/61.
    for _ in range(60):
        breaker.record(was_false_intervention=False)
    breaker.record(was_false_intervention=True)

    assert breaker.tripped is False
    assert breaker.false_intervention_rate == 1 / 61


def test_only_reports_newly_tripped_once():
    breaker = CircuitBreaker(threshold=0.02, min_sample=5)

    results = [breaker.record(was_false_intervention=True) for _ in range(10)]

    assert results.count(True) == 1
