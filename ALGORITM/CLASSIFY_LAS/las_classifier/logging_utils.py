from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path

from . import __version__


LOGGER_NAME = "las_cafiisica"


def configure_logging(base_dir: Path | None = None, debug: bool = False) -> Path:
    root = (base_dir or Path.cwd()).resolve()
    log_dir = root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"LAS_CAFIISICA_{datetime.now():%Y%m%d_%H%M%S}.log"

    logger = logging.getLogger(LOGGER_NAME)
    logger.handlers.clear()
    logger.setLevel(logging.DEBUG if debug else logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler(sys.stderr)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    logger.info("VERSION=%s", __version__)
    return log_path
