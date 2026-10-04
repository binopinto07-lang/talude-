from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tkinter import BOTH, END, LEFT, RIGHT, X, filedialog, messagebox, ttk
import tkinter as tk


APP_VERSION = "0.2.0-talude-r20.4"
PROFILE_NAME = "talude_v1.json"


@dataclass(frozen=True)
class Step:
    id: str
    name: str
    command: str
    timeout_minutes: int = 30
    working_dir: str = "."
    category: str = "build"
    venv: str = "default"
    required: bool = True

    @classmethod
    def from_dict(cls, data: dict) -> "Step":
        return cls(
            id=str(data["id"]),
            name=str(data["name"]),
            command=str(data["command"]),
            timeout_minutes=int(data.get("timeout_minutes", 30)),
            working_dir=str(data.get("working_dir", ".")),
            category=str(data.get("category", "build")),
            venv=str(data.get("venv", "default")),
            required=bool(data.get("required", True)),
        )


class ProjectProfile:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.raw = json.loads(path.read_text(encoding="utf-8"))
        self.name = str(self.raw["name"])
        self.branch = str(self.raw.get("branch", "main"))
        self.required_paths = [str(item) for item in self.raw.get("required_paths", [])]

    def pipeline(self, key: str) -> list[Step]:
        return [Step.from_dict(item) for item in self.raw.get("pipelines", {}).get(key, [])]


class ProcessController:
    def __init__(self) -> None:
        self._process: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()

    def stop(self) -> None:
        with self._lock:
            proc = self._process
        if proc is None or proc.poll() is not None:
            return
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
        *,
        cwd: Path,
        env: dict[str, str],
        timeout_seconds: int,
        on_line,
    ) -> tuple[int, float]:
        args = [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command,
        ]
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        started = time.perf_counter()
        with self._lock:
            self._process = subprocess.Popen(
                args,
                cwd=str(cwd),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=creationflags,
            )
            proc = self._process

        try:
            assert proc.stdout is not None
            deadline = time.monotonic() + max(1, timeout_seconds)
            while True:
                if time.monotonic() > deadline and proc.poll() is None:
                    on_line("[ERRO] Tempo limite excedido. Processo terminado.")
                    self.stop()
                    return 124, time.perf_counter() - started

                line = proc.stdout.readline()
                if line:
                    on_line(line.rstrip("\r\n"))
                elif proc.poll() is not None:
                    break
                else:
                    time.sleep(0.05)

            return int(proc.wait()), time.perf_counter() - started
        finally:
            with self._lock:
                self._process = None


class LocalBuildManager(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"LOCAL BUILD MANAGER — TALUDE R20.4 ({APP_VERSION})")
        self.geometry("1380x860")
        self.minsize(1120, 700)

        self.repo = self._resolve_repo()
        self.profile = self._load_profile(self.repo)
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.controller = ProcessController()
        self.worker: threading.Thread | None = None
        self.active_pipeline: str | None = None
        self.active_steps: list[Step] = []
        self.failed_index: int | None = None
        self.failed_step: Step | None = None

        self._build_ui()
        self._refresh_header()
        self.after(100, self._drain_events)
        self.after(250, self.verify_environment)

    @staticmethod
    def _resolve_repo() -> Path:
        raw = os.environ.get("LBM_EMBEDDED_REPO", "").strip()
        if raw:
            return Path(raw).expanduser().resolve()
        return Path(__file__).resolve().parents[2]

    @staticmethod
    def _load_profile(repo: Path) -> ProjectProfile:
        path = repo / "localbuild" / PROFILE_NAME
        if not path.is_file():
            raise FileNotFoundError(f"Perfil LocalBuild em falta: {path}")
        return ProjectProfile(path)

    def _build_ui(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass

        top = ttk.Frame(self, padding=12)
        top.pack(fill=X)

        self.title_label = ttk.Label(
            top,
            text="TALUDE STUDIO — LOCAL BUILD MANAGER",
            font=("Segoe UI", 17, "bold"),
        )
        self.title_label.pack(anchor="w")

        self.info_label = ttk.Label(top, text="")
        self.info_label.pack(anchor="w", pady=(3, 0))

        source_row = ttk.Frame(top)
        source_row.pack(fill=X, pady=(10, 0))
        ttk.Label(source_row, text="Pasta:").pack(side=LEFT)
        self.path_var = tk.StringVar(value=str(self.repo))
        ttk.Entry(source_row, textvariable=self.path_var).pack(side=LEFT, fill=X, expand=True, padx=8)
        ttk.Button(source_row, text="Selecionar pasta…", command=self.choose_repo).pack(side=RIGHT)

        actions = ttk.Frame(self, padding=(12, 0, 12, 10))
        actions.pack(fill=X)
        ttk.Button(actions, text="Verificar ambiente", command=self.verify_environment).pack(side=LEFT, padx=(0, 6))
        self.test_btn = ttk.Button(actions, text="TESTAR", command=lambda: self.start_pipeline("test"))
        self.test_btn.pack(side=LEFT, padx=6)
        self.build_btn = ttk.Button(actions, text="BUILD", command=lambda: self.start_pipeline("build"))
        self.build_btn.pack(side=LEFT, padx=6)
        self.full_btn = ttk.Button(actions, text="BUILD + TESTES", command=lambda: self.start_pipeline("full"))
        self.full_btn.pack(side=LEFT, padx=6)
        self.retry_btn = ttk.Button(actions, text="Repetir etapa falhada", command=self.retry_failed, state="disabled")
        self.retry_btn.pack(side=LEFT, padx=6)
        self.stop_btn = ttk.Button(actions, text="PARAR", command=self.stop_pipeline, state="disabled")
        self.stop_btn.pack(side=LEFT, padx=6)
        ttk.Button(actions, text="Abrir builds", command=self.open_builds).pack(side=RIGHT)

        body = ttk.Panedwindow(self, orient=tk.HORIZONTAL)
        body.pack(fill=BOTH, expand=True, padx=12, pady=(0, 12))

        left = ttk.Frame(body, padding=6)
        right = ttk.Frame(body, padding=6)
        body.add(left, weight=2)
        body.add(right, weight=3)

        ttk.Label(left, text="Ambiente", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        self.env_tree = ttk.Treeview(left, columns=("state", "component", "detail"), show="headings", height=8)
        self.env_tree.heading("state", text="Estado")
        self.env_tree.heading("component", text="Componente")
        self.env_tree.heading("detail", text="Detalhe")
        self.env_tree.column("state", width=85, stretch=False)
        self.env_tree.column("component", width=180, stretch=False)
        self.env_tree.column("detail", width=420, stretch=True)
        self.env_tree.pack(fill=X, pady=(4, 12))

        ttk.Label(left, text="Pipeline", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        self.step_tree = ttk.Treeview(left, columns=("idx", "state", "step", "time"), show="headings")
        for key, title, width in (
            ("idx", "#", 45),
            ("state", "Estado", 85),
            ("step", "Etapa", 360),
            ("time", "Tempo", 80),
        ):
            self.step_tree.heading(key, text=title)
            self.step_tree.column(key, width=width, stretch=(key == "step"))
        self.step_tree.pack(fill=BOTH, expand=True, pady=(4, 8))

        self.progress = ttk.Progressbar(left, maximum=100, mode="determinate")
        self.progress.pack(fill=X)

        ttk.Label(right, text="Log em tempo real", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        self.log = tk.Text(
            right,
            wrap="none",
            bg="#111827",
            fg="#e5e7eb",
            insertbackground="#e5e7eb",
            font=("Consolas", 9),
        )
        self.log.pack(fill=BOTH, expand=True, pady=(4, 0))

        self.status_var = tk.StringVar(value="Pronto.")
        ttk.Label(self, textvariable=self.status_var, relief="sunken", anchor="w").pack(fill=X, side=tk.BOTTOM)

    def _refresh_header(self) -> None:
        self.info_label.configure(
            text=f"{self.profile.name}  |  branch alvo: {self.profile.branch}  |  perfil rev. {self.profile.raw.get('config_revision', 0)}"
        )
        self.path_var.set(str(self.repo))

    def choose_repo(self) -> None:
        selected = filedialog.askdirectory(initialdir=str(self.repo), title="Selecionar pasta TALUDE")
        if not selected:
            return
        repo = Path(selected).resolve()
        try:
            profile = self._load_profile(repo)
        except Exception as exc:
            messagebox.showerror("Local Build Manager", str(exc))
            return
        self.repo = repo
        self.profile = profile
        os.environ["LBM_EMBEDDED_REPO"] = str(repo)
        self._refresh_header()
        self.verify_environment()

    def _log(self, text: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.events.put(("log", f"[{stamp}] {text}"))

    def _append_log(self, text: str) -> None:
        self.log.insert(END, text + "\n")
        self.log.see(END)

    def _drain_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "log":
                    self._append_log(str(payload))
                elif kind == "step_started":
                    idx, name = payload
                    self._set_step_row(idx, "A correr", name, "")
                elif kind == "step_finished":
                    idx, ok, elapsed = payload
                    step = self.active_steps[idx]
                    self._set_step_row(idx, "OK" if ok else "ERRO", step.name, f"{elapsed:.1f}s")
                elif kind == "finished":
                    ok, message = payload
                    self._pipeline_finished(bool(ok), str(message))
                elif kind == "progress":
                    self.progress["value"] = int(payload)
                elif kind == "status":
                    self.status_var.set(str(payload))
        except queue.Empty:
            pass
        self.after(100, self._drain_events)

    def _set_step_row(self, index: int, state: str, name: str, elapsed: str) -> None:
        item = f"step-{index}"
        values = (index + 1, state, name, elapsed)
        if self.step_tree.exists(item):
            self.step_tree.item(item, values=values)
        else:
            self.step_tree.insert("", END, iid=item, values=values)

    def _populate_steps(self, steps: list[Step]) -> None:
        for item in self.step_tree.get_children():
            self.step_tree.delete(item)
        for idx, step in enumerate(steps):
            self._set_step_row(idx, "Pendente", step.name, "")
        self.progress["value"] = 0

    def verify_environment(self) -> None:
        for item in self.env_tree.get_children():
            self.env_tree.delete(item)

        checks: list[tuple[str, str, str]] = []
        version = sys.version_info
        python_ok = version.major == 3 and version.minor == 12
        checks.append(("OK" if python_ok else "ERRO", "Python", sys.version.split()[0]))

        powershell = self._which("powershell.exe")
        checks.append(("OK" if powershell else "ERRO", "PowerShell", powershell or "não encontrado"))

        git = self._which("git.exe") or self._which("git")
        checks.append(("OK" if git else "AVISO", "Git", git or "opcional para ZIP"))

        source_revision = self.repo / "localbuild" / "SOURCE_REVISION.txt"
        expected = "TALUDE_STUDIO_CLASSIFY_LAS_R20_4_2026_10"
        actual = source_revision.read_text(encoding="utf-8").strip() if source_revision.is_file() else ""
        checks.append(("OK" if actual == expected else "ERRO", "SOURCE_REVISION", actual or "em falta"))

        missing = [path for path in self.profile.required_paths if not (self.repo / path).exists()]
        checks.append(
            (
                "OK" if not missing else "ERRO",
                "Ficheiros obrigatórios",
                "todos presentes" if not missing else ", ".join(missing[:6]) + (" …" if len(missing) > 6 else ""),
            )
        )

        module_version = self.repo / "ALGORITM" / "CLASSIFY_LAS" / "VERSION"
        value = module_version.read_text(encoding="utf-8").strip() if module_version.is_file() else ""
        checks.append(("OK" if value == "R20.4" else "ERRO", "CLASSIFY LAS", value or "em falta"))

        for state, component, detail in checks:
            self.env_tree.insert("", END, values=(state, component, detail))

        errors = [item for item in checks if item[0] == "ERRO"]
        self.status_var.set("Ambiente OK." if not errors else f"Ambiente com {len(errors)} erro(s).")

    @staticmethod
    def _which(name: str) -> str | None:
        try:
            result = subprocess.run(
                ["where", name],
                capture_output=True,
                text=True,
                timeout=5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if result.returncode == 0:
                return result.stdout.splitlines()[0].strip()
        except Exception:
            return None
        return None

    def start_pipeline(self, key: str, *, only_index: int | None = None) -> None:
        if self.worker is not None and self.worker.is_alive():
            messagebox.showwarning("Local Build Manager", "Já existe um pipeline em execução.")
            return

        steps = self.profile.pipeline(key)
        if not steps:
            messagebox.showerror("Local Build Manager", f"Pipeline '{key}' não está configurado.")
            return

        if only_index is not None:
            if only_index < 0 or only_index >= len(steps):
                return
            selected_steps = [steps[only_index]]
        else:
            selected_steps = steps

        self.active_pipeline = key
        self.active_steps = selected_steps
        self.failed_index = None
        self.failed_step = None
        self.retry_btn.configure(state="disabled")
        self._populate_steps(selected_steps)
        self._set_running(True)
        self.status_var.set(f"A executar {key}…")
        self._append_log("")
        self._append_log("=" * 72)
        self._append_log(f"LOCAL BUILD MANAGER | {self.profile.name}")
        self._append_log(f"Repo: {self.repo}")
        self._append_log(f"Pipeline: {key}")
        self._append_log("=" * 72)

        self.worker = threading.Thread(
            target=self._pipeline_worker,
            args=(selected_steps,),
            daemon=True,
            name=f"lbm-{key}",
        )
        self.worker.start()

    def _pipeline_worker(self, steps: list[Step]) -> None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        all_ok = True

        for idx, step in enumerate(steps):
            self.events.put(("step_started", (idx, step.name)))
            self.events.put(("progress", int(100 * idx / max(1, len(steps)))))
            self._log(f">>> ETAPA {idx + 1}/{len(steps)}: {step.name}")

            try:
                python_exe = self._ensure_venv(step.venv)
                command = self._expand_command(step.command, python_exe, timestamp)
                cwd = (self.repo / step.working_dir).resolve()
                if not cwd.exists():
                    raise FileNotFoundError(f"Diretório da etapa não existe: {cwd}")

                env = os.environ.copy()
                if step.venv.lower() not in {"system", "none"}:
                    env["VIRTUAL_ENV"] = str(python_exe.parent.parent)
                    env["PATH"] = str(python_exe.parent) + os.pathsep + env.get("PATH", "")

                self._log("Comando: " + command)
                code, elapsed = self.controller.run(
                    command,
                    cwd=cwd,
                    env=env,
                    timeout_seconds=max(1, step.timeout_minutes) * 60,
                    on_line=self._log,
                )
                ok = code == 0
                self.events.put(("step_finished", (idx, ok, elapsed)))
                self._log(f"{'[OK]' if ok else '[ERRO]'} {step.name} | exit={code} | {elapsed:.1f}s")

                if not ok and step.required:
                    self.failed_index = idx
                    self.failed_step = step
                    all_ok = False
                    break
            except Exception as exc:
                self.failed_index = idx
                self.failed_step = step
                self.events.put(("step_finished", (idx, False, 0.0)))
                self._log(f"[ERRO] {type(exc).__name__}: {exc}")
                all_ok = False
                break

        final_progress = 100 if all_ok else int(100 * max(0, min(len(steps), (self.failed_index or 0))) / max(1, len(steps)))
        self.events.put(("progress", final_progress))
        self.events.put(("finished", (all_ok, "BUILD CONCLUÍDO COM SUCESSO" if all_ok else "PIPELINE TERMINADO COM ERROS")))

    def _ensure_venv(self, profile: str) -> Path:
        if profile.lower() in {"system", "none"}:
            return Path(sys.executable)

        root = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "LBM" / "TALUDE"
        venv = root / profile
        python_exe = venv / "Scripts" / "python.exe"

        if not python_exe.is_file():
            venv.parent.mkdir(parents=True, exist_ok=True)
            self._log(f"A criar ambiente Python: {venv}")
            result = subprocess.run(
                [sys.executable, "-m", "venv", str(venv)],
                cwd=str(self.repo),
                text=True,
                capture_output=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if result.returncode != 0:
                raise RuntimeError(result.stdout + result.stderr)

        return python_exe

    def _expand_command(self, command: str, python_exe: Path, timestamp: str) -> str:
        builds = self.repo / "builds"
        work = builds / "local_work"
        latest = builds / "latest"
        archive = builds / "archive"
        for path in (builds, work, latest, archive):
            path.mkdir(parents=True, exist_ok=True)

        mapping = {
            "%REPO%": str(self.repo),
            "%PYTHON%": str(python_exe),
            "%BUILDS%": str(builds),
            "%WORK%": str(work),
            "%DIST%": str(work / "dist" / "Talude_V1"),
            "%LATEST%": str(latest),
            "%ARCHIVE%": str(archive),
            "%TIMESTAMP%": timestamp,
        }
        result = command
        for token, value in mapping.items():
            result = result.replace(token, value.replace("'", "''"))
        return result

    def retry_failed(self) -> None:
        if self.failed_step is None:
            return

        step = self.failed_step
        self.active_steps = [step]
        self.failed_index = None
        self.failed_step = None
        self.retry_btn.configure(state="disabled")
        self._populate_steps(self.active_steps)
        self._set_running(True)
        self.worker = threading.Thread(
            target=self._pipeline_worker,
            args=(self.active_steps,),
            daemon=True,
            name="lbm-retry",
        )
        self.worker.start()

    def stop_pipeline(self) -> None:
        self.controller.stop()
        self.status_var.set("Pedido de paragem enviado.")

    def _pipeline_finished(self, ok: bool, message: str) -> None:
        self._append_log("")
        self._append_log(message)
        self.status_var.set(message)
        self._set_running(False)
        if not ok and self.failed_index is not None:
            self.retry_btn.configure(state="normal")
        elif ok:
            messagebox.showinfo("Local Build Manager", message)

    def _set_running(self, running: bool) -> None:
        state = "disabled" if running else "normal"
        self.test_btn.configure(state=state)
        self.build_btn.configure(state=state)
        self.full_btn.configure(state=state)
        self.stop_btn.configure(state="normal" if running else "disabled")

    def open_builds(self) -> None:
        path = self.repo / "builds"
        path.mkdir(parents=True, exist_ok=True)
        os.startfile(path)


def main() -> int:
    try:
        app = LocalBuildManager()
    except Exception as exc:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("Local Build Manager", f"{type(exc).__name__}: {exc}")
        root.destroy()
        return 1

    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
