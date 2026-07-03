"""
gunicorn config, auto-loaded when gunicorn runs from the repo root.

Reads the port from the environment in Python, so we never rely on the shell
expanding "$PORT" in a start command — the failure mode on some platforms
(Railway/Nixpacks) where gunicorn is handed the literal string "$PORT".
"""

import os

bind = f"0.0.0.0:{os.environ.get('PORT', '8000')}"
workers = int(os.environ.get("WEB_CONCURRENCY", "3"))
accesslog = "-"
errorlog = "-"
