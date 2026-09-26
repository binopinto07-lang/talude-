from __future__ import annotations

import os
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .config import ExtractConfig
from .engine import extract


class TaludeApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Talude V1 — Crista + Pé Automático")
        self.geometry("760x520")
        self.minsize(700, 480)

        self.input_var = tk.StringVar()
        self.output_var = tk.StringVar(value=str(Path.cwd() / "talude_output"))
        self.cell_var = tk.StringVar(value="0")
        self.low_var = tk.StringVar(value="0")
        self.high_var = tk.StringVar(value="0")
        self.area_var = tk.StringVar(value="4")
        self.length_var = tk.StringVar(value="2")
        self.all_classes_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="Pronto.")
        self._build()

    def _build(self) -> None:
        root = ttk.Frame(self, padding=14)
        root.pack(fill="both", expand=True)
        root.columnconfigure(1, weight=1)

        ttk.Label(root, text="Nuvem LAS/LAZ/XYZ:").grid(row=0, column=0, sticky="w", pady=5)
        ttk.Entry(root, textvariable=self.input_var).grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Button(root, text="Selecionar...", command=self._pick_input).grid(row=0, column=2)

        ttk.Label(root, text="Pasta de saída:").grid(row=1, column=0, sticky="w", pady=5)
        ttk.Entry(root, textvariable=self.output_var).grid(row=1, column=1, sticky="ew", padx=8)
        ttk.Button(root, text="Selecionar...", command=self._pick_output).grid(row=1, column=2)

        params = ttk.LabelFrame(root, text="Parâmetros V1 (0 = AUTO)", padding=10)
        params.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(12, 8))
        for i in range(5):
            params.columnconfigure(i, weight=1)

        fields = [
            ("Cell size (m)", self.cell_var),
            ("Slope LOW (°)", self.low_var),
            ("Slope HIGH (°)", self.high_var),
            ("Área mín. (m²)", self.area_var),
            ("Comprimento mín. (m)", self.length_var),
        ]
        for i, (label, var) in enumerate(fields):
            ttk.Label(params, text=label).grid(row=0, column=i, sticky="w", padx=4)
            ttk.Entry(params, textvariable=var, width=12).grid(row=1, column=i, sticky="ew", padx=4)

        ttk.Checkbutton(
            root,
            text="Processar todas as classes (por defeito usa Ground=2 quando disponível)",
            variable=self.all_classes_var,
        ).grid(row=3, column=0, columnspan=3, sticky="w", pady=6)

        info = (
            "Motor V1: Ground → grelha multiescala → persistence + hysteresis → "
            "FACE_DETECTOR → CRISTA/PÉ → refinamento Z → DXF/GeoJSON/CSV."
        )
        ttk.Label(root, text=info, wraplength=700).grid(row=4, column=0, columnspan=3, sticky="w", pady=7)

        self.run_btn = ttk.Button(root, text="EXTRAIR CRISTA + PÉ", command=self._run)
        self.run_btn.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(8, 10), ipady=7)

        ttk.Label(root, textvariable=self.status_var).grid(row=6, column=0, columnspan=3, sticky="w")
        self.log = tk.Text(root, height=14, wrap="word")
        self.log.grid(row=7, column=0, columnspan=3, sticky="nsew", pady=(6, 0))
        root.rowconfigure(7, weight=1)

    def _pick_input(self) -> None:
        path = filedialog.askopenfilename(
            filetypes=[
                ("Point cloud", "*.las *.laz *.xyz *.txt *.csv"),
                ("Todos", "*.*"),
            ]
        )
        if path:
            self.input_var.set(path)
            if self.output_var.get().endswith("talude_output"):
                src = Path(path)
                self.output_var.set(str(src.parent / (src.stem + "_talude_v1")))

    def _pick_output(self) -> None:
        path = filedialog.askdirectory()
        if path:
            self.output_var.set(path)

    def _append(self, text: str) -> None:
        self.log.insert("end", text + "\n")
        self.log.see("end")

    def _run(self) -> None:
        inp = Path(self.input_var.get().strip())
        if not inp.exists():
            messagebox.showerror("Talude V1", "Selecione uma nuvem válida.")
            return

        out = Path(self.output_var.get().strip())
        try:
            cfg = ExtractConfig(
                cell_size=float(self.cell_var.get()),
                slope_low_deg=float(self.low_var.get()),
                slope_high_deg=float(self.high_var.get()),
                min_face_area_m2=float(self.area_var.get()),
                min_line_length_m=float(self.length_var.get()),
                use_ground_class=not self.all_classes_var.get(),
            )
        except ValueError:
            messagebox.showerror("Talude V1", "Há parâmetros numéricos inválidos.")
            return

        self.run_btn.configure(state="disabled")
        self.status_var.set("A processar...")
        self._append(f"INPUT: {inp}")
        self._append(f"OUTPUT: {out}")

        def worker() -> None:
            try:
                report = extract(inp, out, cfg)
                self.after(0, lambda: self._done(report, out))
            except Exception as exc:
                self.after(0, lambda: self._failed(exc))

        threading.Thread(target=worker, daemon=True).start()

    def _done(self, report: dict, out: Path) -> None:
        self.run_btn.configure(state="normal")
        msg = (
            f'Concluído: {report["faces_detected"]} faces, '
            f'{report["crest_lines"]} cristas, {report["toe_lines"]} pés, '
            f'{report["elapsed_s"]:.1f}s.'
        )
        self.status_var.set(msg)
        self._append(msg)
        self._append(f"Resultados: {out}")

        if messagebox.askyesno("Talude V1", msg + "\n\nAbrir pasta de resultados?"):
            if os.name == "nt":
                os.startfile(out)  # type: ignore[attr-defined]

    def _failed(self, exc: Exception) -> None:
        self.run_btn.configure(state="normal")
        self.status_var.set("Erro no processamento.")
        self._append(f"ERRO: {exc}")
        messagebox.showerror("Talude V1", str(exc))


def main() -> int:
    if "--self-test" in sys.argv:
        import ezdxf
        import laspy
        import numpy
        import scipy

        report = (
            "TALUDE_V1_SELF_TEST=OK\n"
            f"numpy={numpy.__version__}\n"
            f"scipy={scipy.__version__}\n"
            f"laspy={laspy.__version__}\n"
            f"ezdxf={ezdxf.__version__}\n"
        )
        Path("TALUDE_V1_SELF_TEST.txt").write_text(report, encoding="utf-8")
        return 0

    TaludeApp().mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
