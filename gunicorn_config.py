# gunicorn_config.py
import os

# Bind to all interfaces on port 5000
bind = "0.0.0.0:5000"

workers = int(os.environ.get("WEB_CONCURRENCY", "2"))

# Threads per worker. An agent run holds a thread for up to 45 s waiting on
# the model, so with 2 workers x 2 threads four slow questions starved the
# health check and the container was killed mid-run. Requests are I/O-bound
# while waiting on Gemini, so more threads cost little memory.
threads = int(os.environ.get("GUNICORN_THREADS", "8"))

# Timeout for requests (120s gives the AI time to "think" if needed)
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "120"))

# Logging
accesslog = "-"  # Log to stdout
errorlog = "-"   # Log to stderr
loglevel = "info"
