# gunicorn_config.py
import os

# Bind to all interfaces on port 5000
bind = "0.0.0.0:5000"

workers = int(os.environ.get("WEB_CONCURRENCY", "2"))

# Threads per worker (good for handling I/O like AI requests)
threads = int(os.environ.get("GUNICORN_THREADS", "2"))

# Timeout for requests (120s gives the AI time to "think" if needed)
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "120"))

# Logging
accesslog = "-"  # Log to stdout
errorlog = "-"   # Log to stderr
loglevel = "info"
