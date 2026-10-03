"""Standalone measured-ground LAS/LAZ classifier; EPSG:3763.
Requires numpy, scipy, laspy; LAZ additionally requires lazrs.
Does not import or depend on LAS-CAFIISICA, its mantle, or its spatial evidence.
Conservative baseline, NOT a claim of equivalence with the complete R20.3 engine.
"""
from __future__ import annotations
import argparse
import json
from dataclasses import dataclass
from pathlib import Path
import numpy as np
from scipy.ndimage import distance_transform_edt, median_filter, minimum_filter, maximum_filter
import laspy

ALGORITHM_ID = "TALUDE_STANDALONE_GROUND_V1"
API_VERSION = "1.0"
CRS_EPSG = 3763

@dataclass(frozen=True)
class Settings:
    cell_m: float = 0.35
    seed_percentile: float = 10.0
    max_surface_offset_m: float = 0.12
    max_local_relief_m: float = 1.25
    max_unsupported_distance_m: float = 1.0
    chunk_points: int = 750_000
    max_cells: int = 6_000_000

def _index(x, y, xmin, ymin, cell, nx, ny):
    ix = np.floor((x-xmin)/cell).astype(np.int64)
    iy = np.floor((y-ymin)/cell).astype(np.int64)
    ix = np.clip(ix, 0, nx-1)
    iy = np.clip(iy, 0, ny-1)
    return iy*nx+ix

def classify_arrays(x, y, z, return_number=None, number_of_returns=None,
                    settings=Settings()):
    """Classify raw observed points. Return boolean ground and diagnostic metadata.
    Small/medium array API; use classify_file for chunked LAS/LAZ IO.
    """
    x=np.asarray(x,dtype=np.float64); y=np.asarray(y,dtype=np.float64)
    z=np.asarray(z,dtype=np.float64)
    if x.shape!=y.shape or x.shape!=z.shape or x.ndim!=1:
        raise ValueError("x, y, z must be matching one-dimensional arrays")
    valid=np.isfinite(x)&np.isfinite(y)&np.isfinite(z)
    if not valid.any():
        raise ValueError("No finite XYZ points")
    s=settings
    if s.cell_m<=0 or not 0<=s.seed_percentile<=50 or s.chunk_points<1:
        raise ValueError("Invalid settings")
    xmin=float(x[valid].min()); ymin=float(y[valid].min())
    nx=int(np.floor((x[valid].max()-xmin)/s.cell_m))+1
    ny=int(np.floor((y[valid].max()-ymin)/s.cell_m))+1
    if nx*ny>s.max_cells:
        raise MemoryError("Grid too large: increase cell_m or process spatial tiles")
    flat=_index(x[valid],y[valid],xmin,ymin,s.cell_m,nx,ny)
    n=nx*ny
    low=np.full(n,np.inf); high=np.full(n,-np.inf); count=np.zeros(n,dtype=np.int64)
    # Streaming-compatible per-cell extrema and counts; no existing classification authority.
    np.minimum.at(low,flat,z[valid]); np.maximum.at(high,flat,z[valid])
    np.add.at(count,flat,1)
    observed=count.reshape(ny,nx)>0
    low2=low.reshape(ny,nx); high2=high.reshape(ny,nx)
    # Robust low-envelope seed support; do not treat roofs/canopies as ground merely
    # because they are the lowest observed object in a locally isolated cell.
    seed=observed & ((high2-low2)<=s.max_local_relief_m)
    # Require a low-elevation neighborhood to anchor candidates. Unobserved cells
    # are not emitted as ground, even if used internally for nearest-seed lookup.
    if not seed.any():
        return np.zeros(len(z),bool), {"reason":"no_supported_seeds","cells":int(n)}
    seed_z=np.where(seed,low2,np.inf)
    # A 3x3 low envelope permits sloping terrain; no averaging across absent cells.
    neighborhood_low=minimum_filter(seed_z,size=3,mode="constant",cval=np.inf)
    local_max=maximum_filter(np.where(seed,low2,-np.inf),size=3,mode="constant",cval=-np.inf)
    # Disallow isolated high platforms with no neighboring measured lower-level support:
    # This is a conservative heuristic, not definitive roof recognition.
    seed_neighbors=np.zeros((ny,nx),np.int16)
    for dy in (-1,0,1):
        for dx in (-1,0,1):
            if dx==dy==0: continue
            y0=max(0,-dy); y1=min(ny,ny-dy)
            x0=max(0,-dx); x1=min(nx,nx-dx)
            seed_neighbors[y0:y1,x0:x1]+=seed[y0+dy:y1+dy,x0+dx:x1+dx]
    support=seed & (seed_neighbors>=2)
    if not support.any():
        return np.zeros(len(z),bool), {"reason":"no_connected_support","cells":int(n)}
    # The nearest supported measured cell is a *reference*, never a fabricated point.
    dist, nearest=distance_transform_edt(~support,return_indices=True)
    ref=low2[tuple(nearest)]
    cell_ground=observed & (dist*s.cell_m<=s.max_unsupported_distance_m)
    # Reject broad vertical spans typical of vegetation/objects; this can also
    # conservatively reject some real steep faces (explicit limitation).
    cell_ground &= (high2-low2)<=s.max_local_relief_m
    # Nearby steep faces can have a legitimate height step. Compare each actual
    # measured point against its cell's measured low envelope, and limit the
    # supported-cell distance separately instead of imposing global-flat Z.
    good=np.zeros(len(z),dtype=bool)
    zv=z[valid]
    local_low=low[flat]
    reference=ref.ravel()[flat]
    cell_ok=cell_ground.ravel()[flat]
    # A high, unsupported object may sit on a measured cell; accept only its
    # lowest surface neighborhood, not all points in the cell.
    good[valid]=cell_ok & (zv-local_low<=s.max_surface_offset_m) & (
        (np.abs(local_low-reference)<=s.max_local_relief_m) |
        (dist.ravel()[flat]==0)
    )
    return good, {"cells":int(n),"observed_cells":int(observed.sum()),
                  "supported_cells":int(support.sum()),"ground_points":int(good.sum()),
                  "total_points":int(len(z)),"algorithm":ALGORITHM_ID}

def classify_file(source, destination, settings=Settings(), overwrite=False):
    """LAS/LAZ -> LAS/LAZ, with independent point classification.
    Reads source in chunks, writes original dimensions unchanged except class.
    WARNING: current standalone baseline holds XYZ in memory for global spatial
    classification; use spatial tiling for extremely large (>100M) point clouds.
    """
    source=Path(source); destination=Path(destination)
    if not source.is_file(): raise FileNotFoundError(source)
    if source.resolve()==destination.resolve(): raise ValueError("Input and output must differ")
    if destination.exists() and not overwrite: raise FileExistsError(destination)
    with laspy.open(source) as reader:
        crs=reader.header.parse_crs()
        if crs is None or crs.to_epsg()!=CRS_EPSG:
            raise ValueError("Expected explicitly declared EPSG:3763 input CRS")
        xs=[]; ys=[]; zs=[]
        for chunk in reader.chunk_iterator(settings.chunk_points):
            xs.append(np.asarray(chunk.x,dtype=np.float64))
            ys.append(np.asarray(chunk.y,dtype=np.float64))
            zs.append(np.asarray(chunk.z,dtype=np.float64))
    x=np.concatenate(xs); y=np.concatenate(ys); z=np.concatenate(zs)
    ground, stats=classify_arrays(x,y,z,settings=settings)
    del x,y,z,xs,ys,zs
    destination.parent.mkdir(parents=True,exist_ok=True)
    with laspy.open(source) as reader:
        with laspy.open(destination,mode="w",header=reader.header) as writer:
            pos=0
            for chunk in reader.chunk_iterator(settings.chunk_points):
                stop=pos+len(chunk)
                cls=np.asarray(chunk.classification).copy()
                # 2=ground; 1=unclassified. Keep withheld/noise class 7 intact.
                protected=(cls==7)
                cls[~protected]=np.where(ground[pos:stop][~protected],2,1)
                chunk.classification=cls
                writer.write_points(chunk)
                pos=stop
    stats.update({"input":str(source),"output":str(destination),
                  "note":"Experimental standalone baseline; verify with field ground truth."})
    return stats

def main(argv=None):
    p=argparse.ArgumentParser(description="Independent EPSG:3763 LAS/LAZ ground classifier")
    p.add_argument("source"); p.add_argument("destination")
    p.add_argument("--cell",type=float,default=.35)
    p.add_argument("--offset",type=float,default=.12)
    p.add_argument("--overwrite",action="store_true")
    a=p.parse_args(argv)
    result=classify_file(a.source,a.destination,
        Settings(cell_m=a.cell,max_surface_offset_m=a.offset),a.overwrite)
    print(json.dumps(result,indent=2,ensure_ascii=False))
    return result

if __name__=="__main__":
    main()
