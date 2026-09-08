from services.rate_limit import SlidingWindowRateLimiter


def test_sliding_window_rate_limiter_allows_then_throttles_and_recovers():
    limiter = SlidingWindowRateLimiter()

    assert limiter.check("user:1", limit=2, window_seconds=60, now=100) == (True, 0)
    assert limiter.check("user:1", limit=2, window_seconds=60, now=101) == (True, 0)
    allowed, retry_after = limiter.check("user:1", limit=2, window_seconds=60, now=102)
    assert allowed is False
    assert retry_after == 59
    assert limiter.check("user:1", limit=2, window_seconds=60, now=161) == (True, 0)


def test_sliding_window_rate_limiter_is_scoped_by_key():
    limiter = SlidingWindowRateLimiter()

    assert limiter.check("user:1", limit=1, window_seconds=60, now=100)[0] is True
    assert limiter.check("user:1", limit=1, window_seconds=60, now=101)[0] is False
    assert limiter.check("user:2", limit=1, window_seconds=60, now=101)[0] is True
