from __future__ import annotations

WORKING_EPSG = 3763
WORKING_CRS = f"EPSG:{WORKING_EPSG}"


def working_crs() -> str:
    """Return the fixed CRS used by every LAS-CAFIISICA operation."""

    return WORKING_CRS
