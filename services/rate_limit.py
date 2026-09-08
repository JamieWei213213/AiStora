"""Sliding-window rate limiting.

Two backends share one interface, ``check(key, limit, window_seconds)`` ->
``(allowed, retry_after_seconds)``:

* ``SlidingWindowRateLimiter`` keeps the window in process memory. It is
  correct for tests, local development and a single Gunicorn worker.
* ``RedisSlidingWindowRateLimiter`` keeps the window in a Redis sorted set so
  the limit is shared by every worker and every container. Without it each
  Gunicorn worker enforces its own copy of the limit, so two workers quietly
  double every limit and N tasks multiply it again.

``SharedRateLimiter`` is what the routes hold. It resolves the backend lazily
from the Flask app: when a Redis client was configured for sessions
(``app.config["SESSION_REDIS"]``) the limit is shared; otherwise it falls back
to the in-memory limiter. Keys are hashed before use so that a caller can never
grow the store with an attacker-controlled key.
"""

import hashlib
import threading
import time
import uuid
from collections import deque


# Any key longer than this is replaced by its hash. A key built from a request
# body (for example a login email) used to be stored verbatim, so one client
# could grow the limiter's memory by megabytes per request.
_MAX_RAW_KEY_LENGTH = 160

# Upper bound on distinct keys held in memory. Above it the oldest keys are
# discarded, which at worst lets a very early client make one more request.
_MAX_TRACKED_KEYS = 50_000


def normalise_key(key):
    key = str(key)
    if len(key) <= _MAX_RAW_KEY_LENGTH:
        return key
    return f"h:{hashlib.sha256(key.encode('utf-8', 'replace')).hexdigest()}"


class SlidingWindowRateLimiter:
    """Small in-process limiter for local and single-worker deployments."""

    def __init__(self, max_keys=_MAX_TRACKED_KEYS):
        self._events = {}
        self._lock = threading.Lock()
        self._max_keys = max(int(max_keys), 1)

    def check(self, key, limit, window_seconds, now=None):
        limit = max(int(limit), 1)
        window_seconds = max(int(window_seconds), 1)
        current = time.monotonic() if now is None else float(now)
        cutoff = current - window_seconds
        key = normalise_key(key)
        with self._lock:
            events = self._events.get(key)
            if events is None:
                if len(self._events) >= self._max_keys:
                    self._evict(cutoff)
                events = deque()
                self._events[key] = events
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= limit:
                retry_after = max(int(events[0] + window_seconds - current) + 1, 1)
                return False, retry_after
            events.append(current)
            return True, 0

    def _evict(self, cutoff):
        # Drop keys whose windows are empty; if that is not enough, drop the
        # oldest-inserted keys (dict order) until there is room.
        for stale in [k for k, events in self._events.items() if not events or events[-1] <= cutoff]:
            del self._events[stale]
        while len(self._events) >= self._max_keys:
            del self._events[next(iter(self._events))]

    def clear(self):
        with self._lock:
            self._events.clear()


class RedisSlidingWindowRateLimiter:
    """Sliding window on a Redis sorted set, shared across processes."""

    def __init__(self, client, prefix="aistora:ratelimit:"):
        self.client = client
        self.prefix = prefix

    def check(self, key, limit, window_seconds, now=None):
        limit = max(int(limit), 1)
        window_seconds = max(int(window_seconds), 1)
        current = time.time() if now is None else float(now)
        cutoff = current - window_seconds
        redis_key = f"{self.prefix}{normalise_key(key)}"
        member = f"{current:.6f}:{uuid.uuid4().hex[:8]}"

        pipe = self.client.pipeline()
        pipe.zremrangebyscore(redis_key, "-inf", cutoff)
        pipe.zadd(redis_key, {member: current})
        pipe.zcard(redis_key)
        pipe.zrange(redis_key, 0, 0, withscores=True)
        pipe.expire(redis_key, window_seconds + 1)
        _, _, count, oldest, _ = pipe.execute()

        if count > limit:
            # Over the limit: this attempt must not count against the client.
            self.client.zrem(redis_key, member)
            oldest_score = float(oldest[0][1]) if oldest else current
            retry_after = max(int(oldest_score + window_seconds - current) + 1, 1)
            return False, retry_after
        return True, 0

    def clear(self):
        cursor = 0
        while True:
            cursor, keys = self.client.scan(cursor=cursor, match=f"{self.prefix}*", count=500)
            if keys:
                self.client.delete(*keys)
            if cursor == 0:
                break


class SharedRateLimiter:
    """Route-level limiter that uses Redis when the app has it.

    The backend is resolved on first use inside an application context, so the
    module-level limiters in the route files keep working in tests (no app
    context at import time) and pick up Redis automatically in deployment.
    """

    def __init__(self, scope):
        self.scope = scope
        self._fallback = SlidingWindowRateLimiter()
        self._redis = None
        self._resolved_for = None

    def _backend(self):
        try:
            from flask import current_app

            app = current_app._get_current_object()
        except RuntimeError:
            return self._fallback
        client = app.config.get("SESSION_REDIS")
        if client is None:
            return self._fallback
        if self._resolved_for is not client:
            self._redis = RedisSlidingWindowRateLimiter(
                client,
                prefix=f"aistora:ratelimit:{self.scope}:",
            )
            self._resolved_for = client
        return self._redis

    def check(self, key, limit, window_seconds, now=None):
        backend = self._backend()
        try:
            return backend.check(key, limit, window_seconds, now=now)
        except Exception:
            if backend is self._fallback:
                raise
            # Redis hiccup: fail closed to the in-process limiter rather than
            # open. A short local window is better than no limit.
            return self._fallback.check(key, limit, window_seconds, now=now)

    def clear(self):
        self._fallback.clear()
        if self._redis is not None:
            try:
                self._redis.clear()
            except Exception:
                pass
