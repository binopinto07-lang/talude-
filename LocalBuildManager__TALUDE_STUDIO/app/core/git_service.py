from __future__ import annotations

import subprocess
from pathlib import Path


def _run(args: list[str], cwd: Path) -> tuple[int, str]:
    try:
        p = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True, encoding="utf-8", errors="replace")
        text = (p.stdout + "\n" + p.stderr).strip()
        return p.returncode, text
    except FileNotFoundError:
        return 127, "git não encontrado no PATH"


def repo_summary(repo: Path) -> dict[str, str]:
    if not (repo / ".git").exists():
        return {"branch": "—", "commit": "—", "status": "Não é um repositório Git"}
    _, branch = _run(["git", "branch", "--show-current"], repo)
    _, commit = _run(["git", "rev-parse", "--short", "HEAD"], repo)
    _, status = _run(["git", "status", "--porcelain"], repo)
    count = len([x for x in status.splitlines() if x.strip()]) if status else 0
    return {
        "branch": branch.strip() or "—",
        "commit": commit.strip() or "—",
        "status": "Limpo" if count == 0 else f"{count} ficheiro(s) alterado(s)",
    }


def pull(repo: Path) -> tuple[int, str]:
    return _run(["git", "pull", "--ff-only"], repo)
