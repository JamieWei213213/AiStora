"""Daily spend guardrails for the model.

Per-minute rate limits stop bursts but not a patient client: 20 requests a
minute, every minute, for a day is 28,800 agent runs on one account. Each run
may take up to eight model turns, so the bill is bounded only by the caller's
persistence. These counters bound it by day instead:

* per-user requests per day and per-user tokens per day, and
* a global tokens-per-day ceiling for the whole deployment, which is the real
  protection for the API key because it does not care how many accounts the
  abuser registers.

Counters live in Redis when the app has it (shared across workers and tasks)
and in process memory otherwise. Days are UTC so the reset time is the same on
every worker.
"""

import threading
from datetime import datetime, timezone

_DAY_SECONDS = 86_400


class BudgetExceeded(Exception):
    def __init__(self, scope, retry_after):
        super().__init__(scope)
        self.scope = scope
        self.retry_after = retry_after


def _today():
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def seconds_until_utc_midnight(now=None):
    now = now or datetime.now(timezone.utc)
    elapsed = now.hour * 3600 + now.minute * 60 + now.second
    return max(_DAY_SECONDS - elapsed, 1)


class _MemoryCounters:
    def __init__(self):
        self._values = {}
        self._lock = threading.Lock()

    def add(self, key, amount, ttl):
        with self._lock:
            day = _today()
            # Keep only the current day's counters.
            for stale in [k for k in self._values if not k.startswith(day)]:
                del self._values[stale]
            full = f"{day}:{key}"
            self._values[full] = self._values.get(full, 0) + int(amount)
            return self._values[full]

    def get(self, key):
        with self._lock:
            return self._values.get(f"{_today()}:{key}", 0)

    def clear(self):
        with self._lock:
            self._values.clear()


class _RedisCounters:
    def __init__(self, client, prefix="aistora:budget:"):
        self.client = client
        self.prefix = prefix

    def _key(self, key):
        return f"{self.prefix}{_today()}:{key}"

    def add(self, key, amount, ttl):
        redis_key = self._key(key)
        pipe = self.client.pipeline()
        pipe.incrby(redis_key, int(amount))
        pipe.expire(redis_key, int(ttl))
        value, _ = pipe.execute()
        return int(value)

    def get(self, key):
        value = self.client.get(self._key(key))
        return int(value or 0)

    def clear(self):
        cursor = 0
        while True:
            cursor, keys = self.client.scan(cursor=cursor, match=f"{self.prefix}*", count=500)
            if keys:
                self.client.delete(*keys)
            if cursor == 0:
                break


class DailyUsageBudget:
    def __init__(self):
        self._memory = _MemoryCounters()
        self._redis = None
        self._resolved_for = None

    def _store(self):
        try:
            from flask import current_app

            client = current_app.config.get("SESSION_REDIS")
        except RuntimeError:
            client = None
        if client is None:
            return self._memory
        if self._resolved_for is not client:
            self._redis = _RedisCounters(client)
            self._resolved_for = client
        return self._redis

    def _limits(self):
        from config import Config

        return {
            "user_requests": int(getattr(Config, "AGENT_DAILY_REQUESTS_PER_USER", 0) or 0),
            "user_tokens": int(getattr(Config, "AGENT_DAILY_TOKENS_PER_USER", 0) or 0),
            "global_tokens": int(getattr(Config, "AGENT_DAILY_TOKENS_GLOBAL", 0) or 0),
        }

    def _safe(self, method, *args):
        store = self._store()
        try:
            return getattr(store, method)(*args)
        except Exception:
            if store is self._memory:
                raise
            return getattr(self._memory, method)(*args)

    def reserve_request(self, user_id):
        """Count one request for the user; raise if any daily ceiling is hit.

        Called before the model is contacted. Token ceilings are checked
        against usage recorded so far, so a single run can overshoot by at
        most one run's worth of tokens.
        """
        limits = self._limits()
        retry_after = seconds_until_utc_midnight()

        if limits["global_tokens"] and self._safe("get", "tokens:global") >= limits["global_tokens"]:
            raise BudgetExceeded("global_tokens", retry_after)
        if limits["user_tokens"] and self._safe("get", f"tokens:user:{user_id}") >= limits["user_tokens"]:
            raise BudgetExceeded("user_tokens", retry_after)
        if limits["user_requests"]:
            count = self._safe("add", f"requests:user:{user_id}", 1, _DAY_SECONDS)
            if count > limits["user_requests"]:
                # Rejected attempts are not usage; keep the counter honest.
                self._safe("add", f"requests:user:{user_id}", -1, _DAY_SECONDS)
                raise BudgetExceeded("user_requests", retry_after)

    def record_tokens(self, user_id, total_tokens):
        total_tokens = int(total_tokens or 0)
        if total_tokens <= 0:
            return
        self._safe("add", f"tokens:user:{user_id}", total_tokens, _DAY_SECONDS)
        self._safe("add", "tokens:global", total_tokens, _DAY_SECONDS)

    def snapshot(self, user_id):
        limits = self._limits()
        return {
            "requests_today": self._safe("get", f"requests:user:{user_id}"),
            "tokens_today": self._safe("get", f"tokens:user:{user_id}"),
            "daily_request_limit": limits["user_requests"],
            "daily_token_limit": limits["user_tokens"],
        }

    def clear(self):
        self._memory.clear()
        if self._redis is not None:
            try:
                self._redis.clear()
            except Exception:
                pass


usage_budget = DailyUsageBudget()


BUDGET_MESSAGES = {
    "user_requests": "You have reached today's limit of AI requests. It resets at midnight UTC.",
    "user_tokens": "You have reached today's AI usage limit. It resets at midnight UTC.",
    "global_tokens": "AIStora's daily AI budget is exhausted. Please try again after midnight UTC.",
}
