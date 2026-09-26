from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_LOCK = threading.RLock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_safe(item) for item in value]
    return str(value)


def append_project_debug(
    project_path: str | Path,
    event: str,
    details: dict[str, Any] | None = None,
    *,
    source: str = "backend",
    level: str = "INFO",
) -> dict[str, Any]:
    """Write a durable project diagnostic event in text and JSONL."""

    root = Path(project_path).expanduser().resolve()
    log_dir = root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    record = {
        "timestamp": _now(),
        "level": str(level or "INFO").upper(),
        "source": str(source or "backend"),
        "event": str(event),
        "pid": os.getpid(),
        "thread": threading.current_thread().name,
        "details": _safe(details or {}),
    }

    json_line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    human_details = json.dumps(
        record["details"],
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    human_line = (
        f'[{record["timestamp"]}] '
        f'[{record["level"]}] '
        f'[{record["source"]}] '
        f'{record["event"]}'
    )
    if record["details"]:
        human_line += " | " + human_details

    with _LOCK:
        with (log_dir / "PROJECT_DEBUG.log").open(
            "a", encoding="utf-8", errors="replace"
        ) as handle:
            handle.write(human_line + "\n")

        with (log_dir / "PROJECT_DEBUG.jsonl").open(
            "a", encoding="utf-8", errors="replace"
        ) as handle:
            handle.write(json_line + "\n")

    return record
