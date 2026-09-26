from __future__ import annotations

import argparse
import logging
from pathlib import Path

from . import __version__
from .config import ExtractConfig
from .engine import extract


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="talude-v1",
        description="Extração automática 3D de CRISTA e PÉ de talude.",
    )
    p.add_argument("input", help="LAS/LAZ/XYZ/TXT/CSV")
    p.add_argument("-o", "--output", default="talude_output", help="Pasta de saída")
    p.add_argument(
        "--cell-size",
        type=float,
        default=0.0,
        help="Resolução da grelha em metros; 0=AUTO",
    )
    p.add_argument(
        "--slope-low",
        type=float,
        default=0.0,
        help="Threshold fraco de declive; 0=AUTO",
    )
    p.add_argument(
        "--slope-high",
        type=float,
        default=0.0,
        help="Threshold forte de declive; 0=AUTO",
    )
    p.add_argument(
        "--min-area",
        type=float,
        default=4.0,
        help="Área mínima da face inclinada em m²",
    )
    p.add_argument(
        "--min-length",
        type=float,
        default=2.0,
        help="Comprimento mínimo da linha em m",
    )
    p.add_argument(
        "--all-classes",
        action="store_true",
        help="Não limitar à classe Ground(2) em LAS/LAZ",
    )
    p.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    log_path = output / "talude_v1.log"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )

    cfg = ExtractConfig(
        cell_size=args.cell_size,
        slope_low_deg=args.slope_low,
        slope_high_deg=args.slope_high,
        min_face_area_m2=args.min_area,
        min_line_length_m=args.min_length,
        use_ground_class=not args.all_classes,
    )
    report = extract(args.input, output, cfg)
    print(
        f'OK | faces={report["faces_detected"]} | '
        f'cristas={report["crest_lines"]} | '
        f'pes={report["toe_lines"]} | '
        f'output={output.resolve()}'
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
