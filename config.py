# config.py
import os
from dotenv import load_dotenv
from urllib.parse import quote_plus

load_dotenv()


def _env_bool(name, default):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class Config:
    # Read from env, fallback to random only for local dev
    SECRET_KEY = os.environ.get("SECRET_KEY", os.urandom(24))
    
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
    MAX_CONTENT_LENGTH = int(
        os.environ.get("MAX_UPLOAD_BYTES", str(50 * 1024 * 1024))
    )
    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
    GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite")
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
    AGENT_HISTORY_EXAMPLES = int(os.environ.get("AGENT_HISTORY_EXAMPLES", "3"))
    CLEANING_MAX_ROWS = int(os.environ.get("CLEANING_MAX_ROWS", "250000"))
