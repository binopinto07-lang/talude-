from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.spatial import Delaunay

from .tin_delaunay import prepare_xy, robust_delaunay


@dataclass(slots=True)
class TerrainTIN:
    vertices: np.ndarray
    delaunay: Delaunay
    simplices: np.ndarray
    normals: np.ndarray
    plane_d: np.ndarray
    max_edges: np.ndarray
    areas: np.ndarray
    xy_center: np.ndarray
    xy_scale: float

    @classmethod
    def build(cls, vertices: np.ndarray) -> "TerrainTIN":
        prepared, xy, center, scale = prepare_xy(vertices)
        delaunay = robust_delaunay(xy)
        simplices = np.asarray(delaunay.simplices, dtype=np.int64)
        if np.any(simplices < 0) or np.any(
            simplices >= prepared.shape[0]
        ):
            raise RuntimeError(
                "Terrain TIN returned invalid simplex indices"
            )

        tri = np.asarray(prepared[simplices], dtype=np.float64)
        e1 = tri[:, 1] - tri[:, 0]
        e2 = tri[:, 2] - tri[:, 0]
        normals = np.cross(e1, e2)
        lengths = np.linalg.norm(normals, axis=1)
        valid = lengths > 1e-12
        normals[valid] /= lengths[valid, None]
        normals[~valid] = np.array([0.0, 0.0, 1.0])

        flip = normals[:, 2] < 0.0
        normals[flip] *= -1.0
        plane_d = -np.einsum(
            "ij,ij->i",
            normals,
            tri[:, 0],
        )

        edge01 = np.linalg.norm(
            tri[:, 1] - tri[:, 0],
            axis=1,
        )
        edge02 = np.linalg.norm(
            tri[:, 2] - tri[:, 0],
            axis=1,
        )
        edge12 = np.linalg.norm(
            tri[:, 2] - tri[:, 1],
            axis=1,
        )
        max_edges = np.maximum(
            edge01,
            np.maximum(edge02, edge12),
        )
        areas = 0.5 * lengths

        return cls(
            vertices=prepared,
            delaunay=delaunay,
            simplices=simplices,
            normals=normals.astype(np.float64, copy=False),
            plane_d=plane_d.astype(np.float64, copy=False),
            max_edges=max_edges.astype(np.float64, copy=False),
            areas=areas.astype(np.float64, copy=False),
            xy_center=center.astype(np.float64, copy=False),
            xy_scale=float(scale),
        )

    @property
    def triangle_count(self) -> int:
        return int(self.simplices.shape[0])

    def find_simplex(
        self,
        x: np.ndarray,
        y: np.ndarray,
    ) -> np.ndarray:
        query = np.column_stack((x, y)).astype(
            np.float64,
            copy=False,
        )
        query = (
            query - self.xy_center
        ) / self.xy_scale
        return self.delaunay.find_simplex(query)

    def metrics(
        self,
        x: np.ndarray,
        y: np.ndarray,
        z: np.ndarray,
    ) -> dict[str, np.ndarray]:
        simplex = self.find_simplex(x, y)
        valid = simplex >= 0
        safe = np.maximum(simplex, 0)

        normals = self.normals[safe]
        d = self.plane_d[safe]
        signed_distance = (
            normals[:, 0] * x
            + normals[:, 1] * y
            + normals[:, 2] * z
            + d
        )
        signed_distance[~valid] = np.inf

        nz = normals[:, 2]
        predicted_z = np.full(
            x.shape[0],
            np.nan,
            dtype=np.float64,
        )
        stable = valid & (np.abs(nz) > 1e-9)
        predicted_z[stable] = -(
            normals[stable, 0] * x[stable]
            + normals[stable, 1] * y[stable]
            + d[stable]
        ) / nz[stable]
        vertical_residual = z - predicted_z

        return {
            "simplex": simplex,
            "valid": valid,
            "normal": normals,
            "signed_distance": signed_distance,
            "plane_distance": np.abs(signed_distance),
            "predicted_z": predicted_z,
            "vertical_residual": vertical_residual,
            "max_edge": self.max_edges[safe],
        }

    def triangle_centroids(self) -> np.ndarray:
        return np.mean(
            self.vertices[self.simplices],
            axis=1,
        )
