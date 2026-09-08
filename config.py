# config.py
import os
from datetime import timedelta
from dotenv import load_dotenv
from urllib.parse import quote_plus

load_dotenv()


class ConfigurationError(RuntimeError):
    """Raised when the process is not safe to start with the given settings."""


def _env_bool(name, default):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _environment():
    """development | production. APP_ENV wins; FLASK_ENV kept for compatibility."""
    raw = os.environ.get("APP_ENV") or os.environ.get("FLASK_ENV") or "development"
    return "production" if raw.strip().lower() == "production" else "development"


def _secret_key(environment):
    """Return the signing key, refusing to invent one in production.

    A per-process ``os.urandom`` fallback looks harmless but is not: with more
    than one Gunicorn worker each worker signs sessions with a different key,
    so users are logged out at random as requests land on different workers.
    In development that is merely confusing, so we keep the convenience there
    and make it fatal in production.
    """
    key = os.environ.get("SECRET_KEY")
    if key and key.strip():
        return key
    if environment == "production":
        raise ConfigurationError(
            "SECRET_KEY must be set in production. Without it every Gunicorn "
            "worker signs sessions with a different key and users are logged "
            "out at random. Generate one with: python -c "
            "\"import secrets; print(secrets.token_urlsafe(48))\""
        )
    return "dev-only-insecure-key-do-not-use-in-production"


class Config:
    ENVIRONMENT = _environment()
    IS_PRODUCTION = ENVIRONMENT == "production"

    SECRET_KEY = _secret_key(ENVIRONMENT)
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    # Defaults to on in production. If the load balancer still terminates
    # plain HTTP you must opt out explicitly with SESSION_COOKIE_SECURE=false,
    # which is logged loudly at startup. See docs/deployment/.
    SESSION_COOKIE_SECURE = _env_bool("SESSION_COOKIE_SECURE", IS_PRODUCTION)
    SESSION_COOKIE_NAME = os.environ.get("SESSION_COOKIE_NAME", "aistora_session")
    PERMANENT_SESSION_LIFETIME = timedelta(
        hours=int(os.environ.get("SESSION_LIFETIME_HOURS", "12"))
    )

    # --- Server-side session storage -----------------------------------
    # Session payloads (agent memory, pending approvals, cleaning previews)
    # are held server-side. They used to live in the signed cookie, which
    # overflowed the 4 KB browser limit at ~6 tables and silently logged
    # users out. Redis is the deployed backend; the filesystem cache keeps
    # local development and CI working with no extra service.
    REDIS_URL = os.environ.get("REDIS_URL") or os.environ.get("SESSION_REDIS_URL")
    SESSION_TYPE = os.environ.get("SESSION_TYPE") or ("redis" if REDIS_URL else "cachelib")
    SESSION_PERMANENT = True
    # SESSION_USE_SIGNER is deprecated in Flask-Session 0.8 and emits a
    # DeprecationWarning. Session IDs are uuid4, so signing adds nothing here.
    SESSION_KEY_PREFIX = os.environ.get("SESSION_KEY_PREFIX", "aistora:session:")
    SESSION_FILE_DIR = os.environ.get(
        "SESSION_FILE_DIR",
        os.path.join("instance", "sessions"),
    )

    # Content-Security-Policy. Scripts and styles are all same-origin: Tailwind
    # is compiled to static/css/tailwind.css and lucide is vendored under
    # static/js/vendor, so no CDN host needs to be trusted. Chart images come
    # from QuickChart after explicit approval.
    CONTENT_SECURITY_POLICY = os.environ.get(
        "CONTENT_SECURITY_POLICY",
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self'; "
        "font-src 'self' data:; "
        "img-src 'self' data: https://quickchart.io; "
        "connect-src 'self'; "
        "frame-ancestors 'self'; "
        "base-uri 'self'; "
        "form-action 'self'; "
        "object-src 'none'",
    )
    HSTS_SECONDS = int(os.environ.get("HSTS_SECONDS", "31536000" if IS_PRODUCTION else "0"))

    # Alembic owns the schema in production. create_all() stays available for
    # local development and the test suite.
    AUTO_CREATE_TABLES = _env_bool("AUTO_CREATE_TABLES", not IS_PRODUCTION)

    
    UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER", "uploads")
    DATASET_STORAGE_BACKEND = os.environ.get(
        "DATASET_STORAGE_BACKEND",
        "local",
    ).strip().lower()
    AWS_REGION = os.environ.get("AWS_REGION", "us-west-2")
    S3_DATASET_BUCKET = os.environ.get("S3_DATASET_BUCKET")
    S3_DATASET_PREFIX = os.environ.get("S3_DATASET_PREFIX", "datasets")
    S3_ENDPOINT_URL = os.environ.get("S3_ENDPOINT_URL")
    DATASET_CACHE_DIR = os.environ.get(
        "DATASET_CACHE_DIR",
        os.path.join(os.path.abspath(os.sep), "tmp", "aistora-cache")
        if os.name != "nt"
        else os.path.join(UPLOAD_FOLDER, ".cache"),
    )
    # Local cache of S3 objects is evicted least-recently-used past this size.
    DATASET_CACHE_MAX_BYTES = int(
        os.environ.get("DATASET_CACHE_MAX_BYTES", str(2 * 1024 ** 3))
    )
    MAX_CONTENT_LENGTH = int(
        os.environ.get("MAX_UPLOAD_BYTES", str(50 * 1024 * 1024))
    )
    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
    GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")
    GEMINI_ADVANCED_MODEL = os.environ.get(
        "GEMINI_ADVANCED_MODEL",
        "gemini-3.6-flash",
    )
    DATABASE_URL = os.environ.get("DATABASE_URL")
    if DATABASE_URL:
        if DATABASE_URL.startswith("postgres://"):
            DATABASE_URL = DATABASE_URL.replace(
                "postgres://",
                "postgresql+psycopg2://",
                1,
            )
        SQLALCHEMY_DATABASE_URI = DATABASE_URL
    elif os.environ.get("DB_HOST"):
        DB_USER = quote_plus(os.environ.get("DB_USER", "aistora"))
        DB_PASSWORD = quote_plus(os.environ.get("DB_PASSWORD", ""))
        DB_HOST = os.environ["DB_HOST"]
        DB_PORT = int(os.environ.get("DB_PORT", "5432"))
        DB_NAME = os.environ.get("DB_NAME", "aistora")
        DB_SSLMODE = quote_plus(os.environ.get("DB_SSLMODE", "require"))
        SQLALCHEMY_DATABASE_URI = (
            f"postgresql+psycopg2://{DB_USER}:{DB_PASSWORD}"
            f"@{DB_HOST}:{DB_PORT}/{DB_NAME}?sslmode={DB_SSLMODE}"
        )
    else:
        SQLALCHEMY_DATABASE_URI = "sqlite:///local.db"

    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "pool_recycle": 300,
    }

    # Agent budgets can be tuned with environment variables.
    AGENT_MAX_TURNS = int(os.environ.get("AGENT_MAX_TURNS", "8"))
    AGENT_MAX_TOOL_CALLS = int(os.environ.get("AGENT_MAX_TOOL_CALLS", "12"))
    AGENT_MAX_OUTPUT_ROWS = int(os.environ.get("AGENT_MAX_OUTPUT_ROWS", "25"))
    AGENT_MAX_MATERIALIZED_ROWS = int(
        os.environ.get("AGENT_MAX_MATERIALIZED_ROWS", "25000")
    )
    AGENT_MAX_GROUPS = int(os.environ.get("AGENT_MAX_GROUPS", "500"))
    AGENT_TIMEOUT_SECONDS = int(os.environ.get("AGENT_TIMEOUT_SECONDS", "45"))
    AGENT_MODEL_ROUTING = _env_bool("AGENT_MODEL_ROUTING", True)
    AGENT_LLM_MAX_RETRIES = int(os.environ.get("AGENT_LLM_MAX_RETRIES", "2"))
    AGENT_LLM_RETRY_BASE_SECONDS = float(
        os.environ.get("AGENT_LLM_RETRY_BASE_SECONDS", "0.5")
    )
    AGENT_LLM_TIMEOUT_SECONDS = int(
        os.environ.get("AGENT_LLM_TIMEOUT_SECONDS", "30")
    )
    AGENT_LLM_MAX_OUTPUT_TOKENS = int(
        os.environ.get("AGENT_LLM_MAX_OUTPUT_TOKENS", "2048")
    )
    AGENT_HISTORY_EXAMPLES = int(os.environ.get("AGENT_HISTORY_EXAMPLES", "3"))
    AGENT_SCHEMA_PRIVACY = os.environ.get("AGENT_SCHEMA_PRIVACY", "classified")
    AGENT_RESULT_PRIVACY = os.environ.get("AGENT_RESULT_PRIVACY", "full")
    AGENT_MAX_CORRECTIONS = int(os.environ.get("AGENT_MAX_CORRECTIONS", "3"))
    AGENT_MAX_QUERY_CHARS = int(os.environ.get("AGENT_MAX_QUERY_CHARS", "4000"))
    AGENT_RATE_LIMIT_PER_MINUTE = int(
        os.environ.get("AGENT_RATE_LIMIT_PER_MINUTE", "20")
    )
    RELATIONSHIP_RATE_LIMIT_PER_MINUTE = int(
        os.environ.get("RELATIONSHIP_RATE_LIMIT_PER_MINUTE", "5")
    )
    # Daily spend guardrails (0 disables a ceiling). Per-minute limits stop
    # bursts; these stop a patient client, and the global ceiling protects the
    # API key no matter how many accounts are registered. Days reset at UTC
    # midnight.
    AGENT_DAILY_REQUESTS_PER_USER = int(
        os.environ.get("AGENT_DAILY_REQUESTS_PER_USER", "150")
    )
    AGENT_DAILY_TOKENS_PER_USER = int(
        os.environ.get("AGENT_DAILY_TOKENS_PER_USER", "400000")
    )
    AGENT_DAILY_TOKENS_GLOBAL = int(
        os.environ.get("AGENT_DAILY_TOKENS_GLOBAL", "4000000")
    )
    # Upload shape limits. Column names are sent to the model on every turn,
    # so an unbounded header is an unbounded prompt.
    MAX_UPLOAD_COLUMNS = int(os.environ.get("MAX_UPLOAD_COLUMNS", "200"))
    MAX_COLUMN_NAME_CHARS = int(os.environ.get("MAX_COLUMN_NAME_CHARS", "64"))
    MAX_TABLES_PER_PROJECT = int(os.environ.get("MAX_TABLES_PER_PROJECT", "20"))
    MAX_PROJECTS_PER_USER = int(os.environ.get("MAX_PROJECTS_PER_USER", "10"))
    AGENT_INPUT_COST_PER_MILLION = float(os.environ.get("AGENT_INPUT_COST_PER_MILLION", "0"))
    AGENT_OUTPUT_COST_PER_MILLION = float(os.environ.get("AGENT_OUTPUT_COST_PER_MILLION", "0"))
    CLEANING_MAX_ROWS = int(os.environ.get("CLEANING_MAX_ROWS", "250000"))
    CSV_TYPE_SAMPLE_ROWS = int(os.environ.get("CSV_TYPE_SAMPLE_ROWS", "1000"))

    # Deterministic local exploratory data analysis budgets.
    EDA_MAX_TABLES = int(os.environ.get("EDA_MAX_TABLES", "20"))
    EDA_MAX_COLUMNS_PER_TABLE = int(
        os.environ.get("EDA_MAX_COLUMNS_PER_TABLE", "60")
    )
    EDA_MAX_ROWS_PER_TABLE = int(os.environ.get("EDA_MAX_ROWS_PER_TABLE", "100000"))
    EDA_MAX_NUMERIC_COLUMNS = int(os.environ.get("EDA_MAX_NUMERIC_COLUMNS", "25"))
    EDA_MAX_CATEGORICAL_COLUMNS = int(
        os.environ.get("EDA_MAX_CATEGORICAL_COLUMNS", "25")
    )
    EDA_MAX_CORRELATION_COLUMNS = int(
        os.environ.get("EDA_MAX_CORRELATION_COLUMNS", "12")
    )
    EDA_MAX_TOP_CATEGORIES = int(os.environ.get("EDA_MAX_TOP_CATEGORIES", "5"))
    EDA_MAX_DISTINCT_VALUES = int(os.environ.get("EDA_MAX_DISTINCT_VALUES", "25000"))
    EDA_MAX_DUPLICATE_ROWS = int(os.environ.get("EDA_MAX_DUPLICATE_ROWS", "100000"))
    EDA_MAX_CATEGORY_TRACKED_VALUES = int(
        os.environ.get("EDA_MAX_CATEGORY_TRACKED_VALUES", "1000")
    )
    EDA_MIN_OUTLIER_SAMPLE_SIZE = int(
        os.environ.get("EDA_MIN_OUTLIER_SAMPLE_SIZE", "30")
    )
    EDA_TIMEOUT_SECONDS = int(os.environ.get("EDA_TIMEOUT_SECONDS", "30"))
    EDA_RATE_LIMIT_PER_MINUTE = int(os.environ.get("EDA_RATE_LIMIT_PER_MINUTE", "3"))
