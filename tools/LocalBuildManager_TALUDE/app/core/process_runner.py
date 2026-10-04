from __future__ import annotations

import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable


class ProcessRunner:
    def __init__(self) -> None:
        self._process: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()

    def stop(self) -> None:
        with self._lock:
            proc = self._process
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=4)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    def run(
        self,
        command: str,
        cwd: Path,
        on_line: Callable[[str], None],
        timeout_seconds: int | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[int, float]:
        if os.name == "nt":
            args = [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                command,
            ]
        else:
            args = ["bash", "-lc", command]

        started = time.perf_counter()
        proc_env = os.environ.copy()
        if env:
            proc_env.update(env)

        with self._lock:
            self._process = subprocess.Popen(
                args,
                cwd=str(cwd),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=proc_env,
                bufsize=1,
            )
            proc = self._process

        try:
            assert proc.stdout is not None
            for line in iter(proc.stdout.readline, ""):
                if line:
                    on_line(line.rstrip("\r\n"))
                if proc.poll() is not None:
                    break
            proc.stdout.close()
            try:
                code = proc.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                on_line("[ERRO] Tempo limite da etapa excedido. A terminar processo...")
                self.stop()
                code = 124
            return code, time.perf_counter() - started
        finally:
            with self._lock:
                self._process = None
