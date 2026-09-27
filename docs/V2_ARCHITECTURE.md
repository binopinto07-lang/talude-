# Talude Studio V2 — Arquitetura

## Baseline protegida

Branch: `baseline-1.1.7-refine`  
SHA congelado: `bfc21944eb337b57f2d8c31905cb7a0e85b3ed7f`

A baseline é READ ONLY. A V2 é descendente direta desse SHA e deve evoluir
apenas em `v2-experimental-raw-tin-mst`.

## Mapa do código atual

| Componente | Estado | Responsabilidade |
|---|---|---|
| `src/talude_v1/engine.py` | ACTIVE / BASELINE | AUTO global comprovado 1.1.7: grelha, slope multiescala, persistence/hysteresis, faces, CRISTA/PÉ e refinamento |
| `studio/backend/auto_extract.py` | ACTIVE / BASELINE | Job AUTO global que chama `talude_v1.engine.extract` |
| `studio/backend/terrain_face_engine.py` | ACTIVE / BASELINE | Ponte para executar o mesmo detector 1.1.7 numa ROI clicada |
| `core/terrain_face.py` | LEGACY / REFERENCE | Implementação raster/face anterior; não é o motor V2 RAW-TIN |
| `src/talude_v2/engine.py` | EXPERIMENTAL / ACTIVE V2 | RAW Ground → redução robusta → Delaunay TIN → region growing local → boundaries → MST → estações → interseção de superfícies |
| `src/talude_v2/reasons.py` | EXPERIMENTAL / ACTIVE V2 | Reason codes estáveis para rejeições e falhas esperadas |
| `studio/backend/server.py` | ACTIVE | Mantém APIs baseline e expõe endpoint V2 separado |
| `studio/viewer/*` | ACTIVE | Visualização Potree e comparação Baseline/V2 |

## Regra de separação

```text
GLOBAL DETECTOR 1.1.7
        ↓
FACE CANDIDATE / ROI
        ↓
V2 RAW TIN
        ↓
CRISTA / TOE
        ↓
REFINEMENT
        ↓
VECTOR DOCUMENT   (fase futura)
```

A UI nunca deve conter a geometria principal do motor.

## V2 RAW-TIN atual

```text
RAW GROUND LOCAL
   ↓
XY robust reduction + median Z
   ↓
Delaunay TIN
   ↓
triangle normals / slope / downhill
   ↓
seed triangle
   ↓
region growing LOCAL
(neighbour ↔ neighbour)
   ↓
face boundary
   ↓
local crest/toe classification
   ↓
conservative gap links
   ↓
Kruskal MST
   ↓
longest continuous path
   ↓
~1 m stations
   ↓
radius search on RAW cloud
   ↓
robust local surface fitting
   ↓
surface A ∩ surface B
   ↓
XYZ line
```

## Gradient coherence

A V1.1.7 usa uma métrica global de direção em parte do detector. Essa baseline
não é alterada.

Na V2, coerência é local:

- triangle current ↔ neighbour;
- normal change local;
- downhill direction change local;
- classificação crest/toe usa downhill do triângulo local;
- não existe veto baseado numa única média global de direção.

Isto permite que um talude C ou S seja localmente coerente mesmo quando vetores
distantes se anulam.

## Comprimento

A V2 usa comprimento acumulado:

```text
LENGTH_2D = Σ hypot(dx, dy)
LENGTH_3D = Σ sqrt(dx² + dy² + dz²)
```

Nunca usa distância entre primeiro e último vértice como comprimento da linha.

## Reason codes

Reason codes V2 iniciais:

`SUCCESS`, `NO_GROUND`, `LOW_GROUND_SUPPORT`, `INVALID_TIN`,
`NO_FACE`, `FACE_TOO_SMALL`, `FACE_TOO_SHORT`, `LOW_SLOPE`,
`LOW_CONTINUITY`, `BOUNDARY_NOT_FOUND`, `CREST_NOT_FOUND`,
`TOE_NOT_FOUND`, `LINE_TOO_SHORT`, `REFINEMENT_FAILED`,
`MERGE_FAILED`, `CANCELLED`, `INTERNAL_ERROR`.

## Métricas de debug já expostas pela V2

- raw_points;
- tin_points;
- tin_triangles;
- face_triangles;
- face_slope_median_deg;
- local_normal_coherence;
- local_direction_median_deg;
- local_direction_p95_deg;
- crest/toe candidate edges;
- graph edges;
- gap links;
- MST edges;
- path vertices;
- length 2D;
- length 3D;
- refined stations;
- fallback stations;
- plane RMSE median;
- mean/P95/max refinement shift;
- quality score.

## Próximos blocos arquiteturais

A ordem permanece:

1. estabilizar motor V2 local;
2. AUTO GLOBAL V2 híbrido;
3. tiles + halo;
4. refinement robusto/support-aware;
5. Vector Document;
6. layers editáveis;
7. editor/undo/redo;
8. projeto persistente;
9. DXF/SHP/GPKG 3D.
