# app/core/logging.py

from __future__ import annotations

import logging
from app.core.config import settings


def setup_logging() -> None:
    """
    Minimal, production-safe logging setup.

    - Uses stdlib logging (no extra deps)
    - Works well with multi-worker deployments
    - Keeps format consistent across environments
    """
    level = logging.INFO
    if settings.ENV.lower() in {"local", "dev", "development"}:
        level = logging.DEBUG

    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    # Ensure uvicorn loggers follow the same level
    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(logger_name).setLevel(level)
