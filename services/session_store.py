"""Server-side session storage.

Why this module exists
----------------------
AIStora previously kept every piece of session state in Flask's default signed
*client-side* cookie: the project schema, detected relationships, per-project
agent memory, pending approvals and cleaning previews. Browsers cap a cookie at
roughly 4 KB. Measured against the real serializer, a project with six tables of
twenty-five columns plus a full eight-turn agent memory produced a 6.6 KB
cookie, and the application's own configured ceiling (twenty tables, sixty
columns) produced 11.4 KB. Past the limit the browser silently discards the
cookie, so the user is logged out mid-session with no error anywhere.

Session payloads now live server-side. Redis is the deployed backend and is
shared across ECS tasks. A filesystem-backed cachelib store is used when no
Redis URL is configured so that local development, docker-compose without a
cache, and CI all keep working with no extra service.
"""

from flask_session import Session

from services.logger import get_logger


logger = get_logger(__name__)


class SessionBackendError(RuntimeError):
    """Raised when the configured session backend cannot be used."""


def _configure_redis(app):
    from redis import Redis

    url = app.config.get("REDIS_URL")
    if not url:
        raise SessionBackendError(
            "SESSION_TYPE=redis requires REDIS_URL (for example "
            "redis://cache:6379/0)."
        )

    # Flask-Session requires an actual redis.Redis instance. If it is handed a
    # URL string the isinstance check fails, the value is discarded without an
    # error, and it quietly connects to localhost:6379 instead. Build the
    # client here so that misconfiguration surfaces as a connection error
    # against the right host rather than as a confusing localhost failure.
    client = Redis.from_url(
        url,
        socket_timeout=int(app.config.get("REDIS_SOCKET_TIMEOUT", 5)),
        socket_connect_timeout=int(app.config.get("REDIS_CONNECT_TIMEOUT", 5)),
        health_check_interval=30,
    )
    client.ping()
    app.config["SESSION_REDIS"] = client
    return f"redis ({url.split('@')[-1]})"


def _configure_cachelib(app):
    import os

    from cachelib.file import FileSystemCache

    directory = app.config.get("SESSION_FILE_DIR") or "instance/sessions"
    os.makedirs(directory, exist_ok=True)
    # Passing the instance explicitly matters: without SESSION_CACHELIB,
    # Flask-Session falls back to a relative "flask_session" directory next to
    # the working directory and only emits a RuntimeWarning about it.
    app.config["SESSION_TYPE"] = "cachelib"
    app.config["SESSION_CACHELIB"] = FileSystemCache(
        cache_dir=directory,
        threshold=int(app.config.get("SESSION_FILE_THRESHOLD", 5000)),
    )
    return f"cachelib filesystem ({directory})"


def configure_sessions(app):
    """Attach server-side session storage and return a description of it.

    In production an unreachable Redis is fatal: falling back to per-container
    filesystem sessions would mean a user's session existing on one ECS task
    and not another, which presents as random logouts. In development the
    fallback is convenient and is logged.
    """
    requested = str(app.config.get("SESSION_TYPE", "cachelib")).lower()

    if requested == "redis":
        try:
            description = _configure_redis(app)
        except Exception as exc:
            if app.config.get("IS_PRODUCTION"):
                raise SessionBackendError(
                    f"Redis session backend is unavailable: {exc}. Refusing to "
                    "start in production with per-container sessions, which "
                    "would log users out at random as requests move between "
                    "tasks."
                ) from exc
            logger.warning(
                "Redis session backend unavailable (%s); falling back to the "
                "filesystem store for local development",
                exc,
            )
            app.config["SESSION_TYPE"] = "cachelib"
            description = _configure_cachelib(app)
    else:
        description = _configure_cachelib(app)

    Session().init_app(app)
    logger.info("Server-side sessions configured: %s", description)
    return description
