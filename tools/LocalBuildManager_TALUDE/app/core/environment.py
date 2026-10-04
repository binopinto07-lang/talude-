from __future__ import annotations

import hashlib
import os
import re
import subprocess
import urllib.request
from pathlib import Path
from typing import Callable

from packaging.version import Version, InvalidVersion

from .models import Check, ProjectConfig


PYTHON_312_VERSION = "3.12.10"
PYTHON_312_URL = (
    "https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe"
)
PYTHON_312_SHA256 = (
    "67b5635e80ea51072b87941312d00ec8927c4db9ba18938f7ad2d27b328b95fb"
)


def _progress(callback: Callable[[str], None] | None, message: str) -> None:
    if callback:
        callback(message)


def managed_python_root() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / "LBM" / "py312"
    return Path.home() / ".lbm" / "py312"


def managed_python_exe() -> Path:
    root = managed_python_root()
    if os.name == "nt":
        return root / "python.exe"
    return root / "bin" / "python"


def _python_candidates() -> list[list[str]]:
    candidates: list[list[str]] = []
    if os.name == "nt":
        candidates.extend([
            ["py", "-3.12"],
            [str(managed_python_exe())],
        ])
        local = os.environ.get("LOCALAPPDATA")
        if local:
            candidates.append([
                str(Path(local) / "Programs" / "Python" / "Python312" / "python.exe")
            ])
    candidates.append(["python"])
    return candidates


def _probe_python(prefix: list[str]) -> tuple[bool, str]:
    try:
        p = subprocess.run(prefix + ["--version"], capture_output=True, text=True, timeout=10)
        out = (p.stdout + p.stderr).strip()
        match = re.search(r"(\d+\.\d+\.\d+)", out)
        if p.returncode == 0 and match:
            try:
                version = Version(match.group(1))
                if version.major == 3 and version.minor == 12:
                    return True, out
            except InvalidVersion:
                pass
        return False, out
    except Exception as exc:
        return False, str(exc)


def detect_python_312() -> tuple[bool, str, list[str]]:
    for prefix in _python_candidates():
        if len(prefix) == 1 and ("\\" in prefix[0] or "/" in prefix[0]):
            if not Path(prefix[0]).is_file():
                continue
        ok, detail = _probe_python(prefix)
        if ok:
            return True, detail, prefix
    return False, "Python 3.12 não encontrado", []


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().lower()


def install_managed_python_312(
    on_progress: Callable[[str], None] | None = None,
) -> tuple[bool, str, list[str]]:
    if os.name != "nt":
        return False, "Bootstrap automático do Python 3.12 só está disponível no Windows.", []

    root = managed_python_root()
    python_exe = managed_python_exe()
    ok, detail = _probe_python([str(python_exe)]) if python_exe.is_file() else (False, "")
    if ok:
        return True, f"Python gerido existente: {detail} ({python_exe})", [str(python_exe)]

    lbm_root = root.parent
    downloads = lbm_root / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    installer = downloads / f"python-{PYTHON_312_VERSION}-amd64.exe"
    partial = installer.with_suffix(installer.suffix + ".part")

    if installer.is_file() and _sha256(installer) != PYTHON_312_SHA256:
        installer.unlink(missing_ok=True)

    if not installer.is_file():
        _progress(
            on_progress,
            f"[INFO] Python 3.12 não encontrado. A transferir Python {PYTHON_312_VERSION} oficial...",
        )
        partial.unlink(missing_ok=True)
        try:
            with urllib.request.urlopen(PYTHON_312_URL, timeout=60) as response, partial.open("wb") as out:
                while True:
                    block = response.read(1024 * 1024)
                    if not block:
                        break
                    out.write(block)
        except Exception as exc:
            partial.unlink(missing_ok=True)
            return False, f"Falha ao descarregar Python 3.12 de python.org: {exc}", []

        actual = _sha256(partial)
        if actual != PYTHON_312_SHA256:
            partial.unlink(missing_ok=True)
            return False, (
                "Checksum SHA-256 inválido no instalador Python 3.12. "
                f"Esperado={PYTHON_312_SHA256} Obtido={actual}"
            ), []
        partial.replace(installer)
        _progress(on_progress, "[OK] Instalador Python 3.12 descarregado e SHA-256 validado.")

    root.parent.mkdir(parents=True, exist_ok=True)
    _progress(on_progress, f"[INFO] A instalar Python {PYTHON_312_VERSION} em {root}...")
    args = [
        str(installer),
        "/quiet",
        "InstallAllUsers=0",
        f"TargetDir={root}",
        "Include_doc=0",
        "Include_debug=0",
        "Include_dev=1",
        "Include_exe=1",
        "Include_launcher=0",
        "InstallLauncherAllUsers=0",
        "Include_lib=1",
        "Include_pip=1",
        "Include_symbols=0",
        "Include_tcltk=0",
        "Include_test=0",
        "Include_tools=1",
        "PrependPath=0",
        "Shortcuts=0",
        "AssociateFiles=0",
    ]
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=600)
    except Exception as exc:
        return False, f"Falha ao executar instalador Python 3.12: {exc}", []

    if proc.returncode not in {0, 3010}:
        output = (proc.stdout + "\n" + proc.stderr).strip()
        return False, output or f"Instalador Python terminou com código {proc.returncode}", []

    ok, detail = _probe_python([str(python_exe)])
    if not ok:
        return False, f"Python foi instalado mas não pôde ser validado em {python_exe}: {detail}", []

    _progress(on_progress, f"[OK] Python gerido pronto: {detail} ({python_exe})")
    return True, f"Python gerido instalado: {detail} ({python_exe})", [str(python_exe)]


def run_check(check: Check, repo: Path) -> tuple[bool, str]:
    try:
        if check.command.strip() == "@lbm:python312":
            ok, detail, _ = detect_python_312()
            if ok:
                return True, detail
            return True, "Python 3.12 será instalado automaticamente ao iniciar TEST/BUILD."

        if subprocess.os.name == "nt":
            args = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", check.command]
        else:
            args = ["bash", "-lc", check.command]
        p = subprocess.run(args, cwd=str(repo), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
        output = (p.stdout + "\n" + p.stderr).strip()
        return p.returncode == 0, output
    except Exception as exc:
        return False, str(exc)


def venv_dir(config: ProjectConfig, repo: Path, profile: str) -> Path:
    # PySide6 contém ficheiros QML com caminhos internos muito longos.
    # Usamos deliberadamente uma raiz curta para evitar MAX_PATH.
    custom_root = os.environ.get("LOCAL_BUILD_MANAGER_VENV_ROOT", "").strip()
    if custom_root:
        base = Path(custom_root)
    elif os.environ.get("LOCALAPPDATA"):
        base = Path(os.environ["LOCALAPPDATA"]) / "LBM" / "v"
    else:
        base = Path.home() / ".lbm" / "v"

    repo_key = hashlib.sha1(str(repo.resolve()).lower().encode("utf-8", errors="ignore")).hexdigest()[:8]
    safe_profile = re.sub(r"[^A-Za-z0-9_.-]+", "_", profile or "default")[:16]
    return base / repo_key / safe_profile


def venv_python_path(config: ProjectConfig, repo: Path, profile: str) -> Path:
    root = venv_dir(config, repo, profile)
    if os.name == "nt":
        return root / "Scripts" / "python.exe"
    return root / "bin" / "python"


def ensure_venv(
    config: ProjectConfig,
    repo: Path,
    profile: str = "default",
    on_progress: Callable[[str], None] | None = None,
) -> tuple[bool, str, Path]:
    if profile.lower() in {"none", "system"}:
        ok, detail, prefix = detect_python_312()
        if not ok and bool(config.raw.get("auto_bootstrap_python", False)):
            ok, detail, prefix = install_managed_python_312(on_progress=on_progress)
        if not ok:
            return False, detail, Path()
        exe = Path(prefix[0])
        return True, f"Ambiente de sistema: {detail}", exe

    root = venv_dir(config, repo, profile)
    python_exe = venv_python_path(config, repo, profile)
    if python_exe.exists():
        return True, f"Ambiente {profile} existente: {python_exe} (path={len(str(root))} chars)", python_exe

    ok, detail, prefix = detect_python_312()
    if not ok and bool(config.raw.get("auto_bootstrap_python", False)):
        ok, detail, prefix = install_managed_python_312(on_progress=on_progress)
    if not ok:
        return False, detail, python_exe

    try:
        root.parent.mkdir(parents=True, exist_ok=True)
        _progress(on_progress, f"[INFO] A criar ambiente Python '{profile}' em {root}...")
        p = subprocess.run(prefix + ["-m", "venv", str(root)], cwd=str(repo), capture_output=True, text=True, timeout=180)
        output = (p.stdout + "\n" + p.stderr).strip()
        if p.returncode != 0:
            return False, output or f"Falha ao criar ambiente {profile}", python_exe
        return True, f"Ambiente {profile} criado com {detail}: {python_exe} (path={len(str(root))} chars)", python_exe
    except Exception as exc:
        return False, str(exc), python_exe
