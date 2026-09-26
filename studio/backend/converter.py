from __future__ import annotations

import json
import os
import re
import shutil
import struct
import subprocess
import threading
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import laspy

from .paths import converter_executable
from .project_store import ProjectStore


LAS_BBOX_OFFSET = 179
LAS_BBOX_SIZE = 48
LAS_BBOX_STRUCT = struct.Struct("<6d")
BBOX_ERROR_MARKERS = (
    "encountered point outside bounding box",
    "requires a valid bounding box",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, dict[str, Any]] = {}

    def create(self, title: str) -> str:
        job_id = uuid.uuid4().hex
        with self._lock:
            self._jobs[job_id] = {
                "id": job_id,
                "title": title,
                "status": "queued",
                "progress": 0,
                "message": "Na fila",
                "created_at": _now(),
                "updated_at": _now(),
                "result": None,
                "error": None,
            }
        return job_id

    def update(self, job_id: str, **changes: Any) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.update(changes)
            job["updated_at"] = _now()

    def get(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            if job_id not in self._jobs:
                raise KeyError(job_id)
            return dict(self._jobs[job_id])


jobs = JobRegistry()


def _read_cloud_metadata(path: Path) -> dict[str, Any]:
    with laspy.open(path) as reader:
        header = reader.header
        crs = None
        try:
            parsed = header.parse_crs()
            if parsed is not None:
                crs = parsed.to_wkt()
        except Exception:
            pass

        return {
            "point_count": int(header.point_count),
            "point_format": str(header.point_format),
            "las_version": str(header.version),
            "mins": [float(v) for v in header.mins],
            "maxs": [float(v) for v in header.maxs],
            "scales": [float(v) for v in header.scales],
            "offsets": [float(v) for v in header.offsets],
            "crs_wkt": crs,
        }


def _append_log(log_path: Path, text: str) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8", errors="replace") as fh:
        fh.write(text.rstrip() + "\n")


def _read_bbox_bytes(source: Path) -> bytes:
    with source.open("rb") as fh:
        fh.seek(LAS_BBOX_OFFSET)
        data = fh.read(LAS_BBOX_SIZE)

    if len(data) != LAS_BBOX_SIZE:
        raise RuntimeError("Cabeçalho LAS demasiado curto para ler o bounding box.")

    return data


def _decode_bbox(data: bytes) -> tuple[list[float], list[float]]:
    max_x, min_x, max_y, min_y, max_z, min_z = LAS_BBOX_STRUCT.unpack(data)
    return [min_x, min_y, min_z], [max_x, max_y, max_z]


def _encode_bbox(mins: list[float], maxs: list[float]) -> bytes:
    return LAS_BBOX_STRUCT.pack(
        float(maxs[0]),
        float(mins[0]),
        float(maxs[1]),
        float(mins[1]),
        float(maxs[2]),
        float(mins[2]),
    )


def _write_bbox_bytes(source: Path, data: bytes) -> None:
    if len(data) != LAS_BBOX_SIZE:
        raise ValueError("Bounding box LAS inválido.")

    with source.open("r+b") as fh:
        fh.seek(LAS_BBOX_OFFSET)
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())


def _bbox_margin(scales: list[float]) -> list[float]:
    # PotreeConverter é estrito. Alguns exportadores LAS deixam o bbox apenas
    # alguns micrómetros abaixo/acima dos pontos por arredondamento. 1 cm é
    # irrelevante para o levantamento e muito acima desses erros numéricos.
    return [max(abs(float(scale)) * 16.0, 0.01) for scale in scales]


def _parse_offending_point(lines: list[str]) -> list[float] | None:
    pattern = re.compile(
        r"point:\s*"
        r"([-+0-9.eE]+)\s*,\s*"
        r"([-+0-9.eE]+)\s*,\s*"
        r"([-+0-9.eE]+)"
    )

    for line in reversed(lines):
        match = pattern.search(line)
        if match:
            return [
                float(match.group(1)),
                float(match.group(2)),
                float(match.group(3)),
            ]

    return None


def _is_bbox_failure(lines: list[str]) -> bool:
    text = "\n".join(lines).lower()
    return any(marker in text for marker in BBOX_ERROR_MARKERS)


class TemporaryLasBBoxRepair:
    """
    Expande temporariamente os seis doubles min/max do cabeçalho LAS/LAZ.

    Antes da alteração, os 48 bytes originais são guardados num journal.
    A restauração ocorre sempre em __exit__. Se a aplicação for terminada à
    força, _recover_pending_bbox_repairs restaura o journal na próxima importação
    desse projeto.
    """

    def __init__(
        self,
        source: Path,
        project_path: Path,
        cloud_id: str,
        metadata: dict[str, Any],
        log_path: Path,
    ) -> None:
        self.source = source
        self.project_path = project_path
        self.cloud_id = cloud_id
        self.metadata = metadata
        self.log_path = log_path
        self.journal = project_path / "cache" / f"bbox_repair_{cloud_id}.json"
        self.original_bytes: bytes | None = None
        self.current_mins: list[float] | None = None
        self.current_maxs: list[float] | None = None

    def start(self) -> None:
        if self.source.name.lower().endswith(".copc.laz"):
            raise RuntimeError(
                "Reparo temporário de bbox COPC ainda não é permitido."
            )

        self.original_bytes = _read_bbox_bytes(self.source)
        original_mins, original_maxs = _decode_bbox(self.original_bytes)
        margins = _bbox_margin(
            self.metadata.get("scales") or [0.001, 0.001, 0.001]
        )

        self.current_mins = [
            original_mins[i] - margins[i]
            for i in range(3)
        ]
        self.current_maxs = [
            original_maxs[i] + margins[i]
            for i in range(3)
        ]

        self.journal.parent.mkdir(parents=True, exist_ok=True)
        self.journal.write_text(
            json.dumps(
                {
                    "schema": "cloud-to-lines-bbox-repair/v1",
                    "source": str(self.source),
                    "created_at": _now(),
                    "offset": LAS_BBOX_OFFSET,
                    "original_hex": self.original_bytes.hex(),
                    "original_mins": original_mins,
                    "original_maxs": original_maxs,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        self._apply()

        _append_log(
            self.log_path,
            "AUTO_BBOX_REPAIR: cabeçalho expandido temporariamente "
            f"mins={self.current_mins} maxs={self.current_maxs}",
        )

    def include_point(self, point: list[float]) -> None:
        if self.current_mins is None or self.current_maxs is None:
            return

        margins = _bbox_margin(
            self.metadata.get("scales") or [0.001, 0.001, 0.001]
        )
        changed = False

        for i in range(3):
            candidate_min = point[i] - margins[i]
            candidate_max = point[i] + margins[i]

            if candidate_min < self.current_mins[i]:
                self.current_mins[i] = candidate_min
                changed = True

            if candidate_max > self.current_maxs[i]:
                self.current_maxs[i] = candidate_max
                changed = True

        if changed:
            self._apply()
            _append_log(
                self.log_path,
                "AUTO_BBOX_REPAIR: bbox expandido para incluir ponto "
                f"{point}; mins={self.current_mins} maxs={self.current_maxs}",
            )

    def _apply(self) -> None:
        assert self.current_mins is not None
        assert self.current_maxs is not None

        _write_bbox_bytes(
            self.source,
            _encode_bbox(self.current_mins, self.current_maxs),
        )

    def restore(self) -> None:
        if self.original_bytes is None:
            return

        _write_bbox_bytes(self.source, self.original_bytes)

        _append_log(
            self.log_path,
            "AUTO_BBOX_REPAIR: 48 bytes originais do cabeçalho restaurados.",
        )

        try:
            self.journal.unlink()
        except FileNotFoundError:
            pass

    def __enter__(self) -> "TemporaryLasBBoxRepair":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.restore()


def _recover_pending_bbox_repairs(project_path: Path, log_path: Path) -> None:
    cache = project_path / "cache"
    if not cache.exists():
        return

    for journal in cache.glob("bbox_repair_*.json"):
        try:
            data = json.loads(journal.read_text(encoding="utf-8"))
            source = Path(data["source"])
            original = bytes.fromhex(data["original_hex"])

            if source.exists() and len(original) == LAS_BBOX_SIZE:
                _write_bbox_bytes(source, original)
                _append_log(
                    log_path,
                    f"AUTO_BBOX_RECOVERY: cabeçalho restaurado de {source}",
                )

            journal.unlink(missing_ok=True)

        except Exception as exc:
            _append_log(
                log_path,
                f"AUTO_BBOX_RECOVERY_WARNING: {journal}: "
                f"{type(exc).__name__}: {exc}",
            )


def _run_converter(
    command: list[str],
    log_path: Path,
    job_id: str,
    attempt_label: str,
) -> tuple[int, list[str]]:
    _append_log(log_path, "")
    _append_log(log_path, f"=== {attempt_label} ===")
    _append_log(log_path, "COMMAND: " + subprocess.list2cmdline(command))

    tail: list[str] = []

    proc = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )

    assert proc.stdout is not None

    for line in proc.stdout:
        clean = line.rstrip("\r\n")
        _append_log(log_path, clean)

        tail.append(clean)
        if len(tail) > 400:
            del tail[:100]

        lower = clean.lower()

        if "counting" in lower:
            jobs.update(
                job_id,
                progress=22,
                message="A validar e organizar pontos…",
            )
        elif "indexing" in lower:
            jobs.update(
                job_id,
                progress=45,
                message=clean[:180],
            )
        elif "sampling" in lower or "building" in lower:
            jobs.update(
                job_id,
                progress=65,
                message=clean[:180],
            )
        elif "writing" in lower or "flush" in lower:
            jobs.update(
                job_id,
                progress=84,
                message=clean[:180],
            )

    return proc.wait(), tail


def start_import(
    store: ProjectStore,
    project_id: str,
    source_path: str,
) -> str:
    source = Path(source_path).expanduser().resolve()

    if not source.exists():
        raise FileNotFoundError(source)

    if not (
        source.name.lower().endswith(".las")
        or source.name.lower().endswith(".laz")
        or source.name.lower().endswith(".copc.laz")
    ):
        raise ValueError("Formato suportado: LAS, LAZ ou COPC.LAZ.")

    job_id = jobs.create(f"Importar {source.name}")
    store.debug_event(
        project_id,
        "cloud.import_queued",
        {
            "job_id": job_id,
            "source_path": str(source),
            "source_name": source.name,
            "source_size": source.stat().st_size,
        },
        source="converter",
    )

    thread = threading.Thread(
        target=_import_worker,
        args=(store, project_id, source, job_id),
        daemon=True,
        name=f"ctl-import-{job_id[:8]}",
    )
    thread.start()

    return job_id


def _import_worker(
    store: ProjectStore,
    project_id: str,
    source: Path,
    job_id: str,
) -> None:
    cloud_id = uuid.uuid4().hex
    project = None
    log_path = None

    try:
        project = store.get(project_id)

        cloud_dir = project.path / "clouds" / cloud_id
        cloud_dir.mkdir(parents=True, exist_ok=True)

        log_path = project.path / "logs" / f"import_{cloud_id}.log"

        store.debug_event(
            project_id,
            "cloud.import_started",
            {
                "job_id": job_id,
                "cloud_id": cloud_id,
                "source_path": str(source),
                "import_log": str(log_path),
            },
            source="converter",
        )

        _recover_pending_bbox_repairs(project.path, log_path)

        jobs.update(
            job_id,
            status="running",
            progress=5,
            message="A ler cabeçalho LAS/LAZ",
        )

        metadata = _read_cloud_metadata(source)
        store.debug_event(
            project_id,
            "cloud.metadata_read",
            {
                "job_id": job_id,
                "cloud_id": cloud_id,
                "point_count": metadata.get("point_count"),
                "point_format": metadata.get("point_format"),
                "las_version": metadata.get("las_version"),
                "mins": metadata.get("mins"),
                "maxs": metadata.get("maxs"),
                "scales": metadata.get("scales"),
                "offsets": metadata.get("offsets"),
                "crs_present": bool(metadata.get("crs_wkt")),
            },
            source="converter",
        )

        source_manifest = {
            "id": cloud_id,
            "name": source.stem,
            "source_path": str(source),
            "source_size": source.stat().st_size,
            "imported_at": _now(),
            **metadata,
        }

        (project.path / "sources" / f"{cloud_id}.json").write_text(
            json.dumps(
                source_manifest,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        converter = converter_executable()

        if not converter.exists():
            raise FileNotFoundError(
                "PotreeConverter não encontrado. "
                "Execute v2/scripts/bootstrap_vendor.ps1."
            )

        jobs.update(
            job_id,
            progress=12,
            message="A criar octree Potree — o LAS original permanece intacto",
        )

        command = [
            str(converter),
            str(source),
            "-o",
            str(cloud_dir),
            "--overwrite",
        ]

        code, tail = _run_converter(
            command,
            log_path,
            job_id,
            "POTREE CONVERSION — tentativa normal",
        )

        auto_bbox_repaired = False

        if code != 0 and _is_bbox_failure(tail):
            store.debug_event(
                project_id,
                "cloud.bbox_repair_required",
                {
                    "job_id": job_id,
                    "cloud_id": cloud_id,
                    "source_path": str(source),
                    "import_log": str(log_path),
                },
                source="converter",
                level="WARNING",
            )
            if source.name.lower().endswith(".copc.laz"):
                raise RuntimeError(
                    "O COPC tem um bounding box inválido. "
                    "Repare/exporte a nuvem antes da conversão."
                )

            jobs.update(
                job_id,
                progress=18,
                message=(
                    "Bounding box LAS inválido — "
                    "reparo temporário automático…"
                ),
            )

            _append_log(
                log_path,
                "AUTO_BBOX_REPAIR: PotreeConverter detetou bbox inválido; "
                "iniciado reparo temporário reversível.",
            )

            try:
                with TemporaryLasBBoxRepair(
                    source=source,
                    project_path=project.path,
                    cloud_id=cloud_id,
                    metadata=metadata,
                    log_path=log_path,
                ) as repair:

                    for retry in range(1, 4):
                        shutil.rmtree(
                            cloud_dir,
                            ignore_errors=True,
                        )
                        cloud_dir.mkdir(
                            parents=True,
                            exist_ok=True,
                        )

                        code, tail = _run_converter(
                            command,
                            log_path,
                            job_id,
                            (
                                "POTREE CONVERSION — "
                                f"bbox reparado, tentativa {retry}"
                            ),
                        )

                        if code == 0:
                            auto_bbox_repaired = True
                            break

                        if not _is_bbox_failure(tail):
                            break

                        point = _parse_offending_point(tail)
                        if point is None:
                            break

                        repair.include_point(point)

            except PermissionError as exc:
                raise RuntimeError(
                    "O LAS tem bounding box inválido e não foi possível "
                    "aplicar o reparo temporário porque o ficheiro não permite "
                    "escrita. Copie a nuvem para uma pasta local com permissões "
                    "de escrita."
                ) from exc

        if code != 0:
            raise RuntimeError(
                f"PotreeConverter terminou com código {code}. "
                f"Ver log: {log_path}"
            )

        potree_metadata = cloud_dir / "metadata.json"

        if not potree_metadata.exists():
            raise RuntimeError(
                "Conversão terminou sem metadata.json."
            )

        manifest = store.manifest(project_id)
        manifest.setdefault("clouds", [])

        manifest["clouds"] = [
            c
            for c in manifest["clouds"]
            if c.get("id") != cloud_id
        ]

        manifest["clouds"].append(
            {
                "id": cloud_id,
                "name": source.stem,
                "source_path": str(source),
                "potree_path": str(cloud_dir),
                "auto_bbox_repaired_for_conversion": auto_bbox_repaired,
                **metadata,
            }
        )

        if not manifest.get("crs") and metadata.get("crs_wkt"):
            manifest["crs"] = {
                "wkt": metadata["crs_wkt"],
            }

        store.save_manifest(
            project_id,
            manifest,
        )

        store.debug_event(
            project_id,
            "cloud.import_completed",
            {
                "job_id": job_id,
                "cloud_id": cloud_id,
                "source_path": str(source),
                "potree_path": str(cloud_dir),
                "point_count": metadata.get("point_count"),
                "auto_bbox_repaired_for_conversion": auto_bbox_repaired,
                "import_log": str(log_path),
            },
            source="converter",
        )

        jobs.update(
            job_id,
            status="completed",
            progress=100,
            message=(
                "Nuvem pronta · bbox corrigido temporariamente "
                "durante conversão"
                if auto_bbox_repaired
                else "Nuvem pronta"
            ),
            result={
                "project_id": project_id,
                "cloud_id": cloud_id,
                "auto_bbox_repaired_for_conversion": auto_bbox_repaired,
            },
        )

    except Exception as exc:
        if project is not None:
            try:
                store.debug_event(
                    project_id,
                    "cloud.import_failed",
                    {
                        "job_id": job_id,
                        "cloud_id": cloud_id,
                        "source_path": str(source),
                        "import_log": str(log_path) if log_path else None,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                        "traceback": traceback.format_exc(),
                    },
                    source="converter",
                    level="ERROR",
                )
            except Exception:
                pass

        jobs.update(
            job_id,
            status="failed",
            progress=100,
            message="Falha na importação",
            error=f"{type(exc).__name__}: {exc}",
        )
