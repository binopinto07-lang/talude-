from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(slots=True)
class ExtractConfig:
    """Parâmetros do BREAKLINE_ENGINE_V1.

    Valores 0 em cell_size e slope_* ativam seleção automática.
    """

    cell_size: float = 0.0
    slope_low_deg: float = 0.0
    slope_high_deg: float = 0.0
    min_face_area_m2: float = 4.0
    min_line_length_m: float = 2.0
    smooth_sigmas_cells: tuple[float, ...] = (0.8, 1.5, 3.0)
    min_scale_persistence: int = 2
    morphology_radius_cells: int = 1
    min_gradient_coherence: float = 0.50
    cross_section_bin_factor: float = 1.0
    line_smooth_window: int = 11
    refine_radius_factor: float = 2.5
    refine_min_points: int = 8
    use_ground_class: bool = True
    ground_class: int = 2
    classification_filter: tuple[int, ...] | None = None
    max_points_for_spacing: int = 80_000
    random_seed: int = 1337

    def to_dict(self) -> dict:
        d = asdict(self)
        d["smooth_sigmas_cells"] = list(self.smooth_sigmas_cells)
        if self.classification_filter is not None:
            d["classification_filter"] = list(self.classification_filter)
        return d
