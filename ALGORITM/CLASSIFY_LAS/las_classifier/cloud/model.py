from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import laspy
import numpy as np
from numpy.typing import NDArray


UNKNOWN_CLASS = np.uint8(0)


@dataclass(slots=True)
class CloudModel:
    """Loaded LAS/LAZ cloud without unnecessary full-cloud XYZ duplication.

    The LAS object remains the authoritative point store. Source classification
    is preserved. Working classes and Nx3 XYZ are allocated only when a later
    processing stage explicitly asks for them.
    """

    path: Path
    las: laspy.LasData
    original_class: NDArray[np.uint8]
    _working_class: NDArray[np.uint8] | None = field(
        default=None, repr=False
    )
    _xyz_cache: NDArray[np.float64] | None = field(
        default=None, repr=False
    )

    @property
    def point_count(self) -> int:
        return int(len(self.las.points))

    @property
    def working_class(self) -> NDArray[np.uint8]:
        if self._working_class is None:
            self._working_class = np.full(
                self.point_count, UNKNOWN_CLASS, dtype=np.uint8
            )
        return self._working_class

    @property
    def xyz(self) -> NDArray[np.float64]:
        if self._xyz_cache is None:
            self._xyz_cache = np.column_stack(
                (self.las.x, self.las.y, self.las.z)
            ).astype(np.float64, copy=False)
        return self._xyz_cache
