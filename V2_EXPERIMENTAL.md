# Talude Studio V2 Experimental — AUTO GLOBAL + RAW TIN + MST

> Branch: `v2-experimental-raw-tin-mst`
>
> Baseline protegida: commit `bfc21944eb337b57f2d8c31905cb7a0e85b3ed7f`
> (Talude Studio 1.1.7-refine).

## Regra principal

A baseline 1.1.7 continua **READ ONLY** e não é alterada.

Na **Exp2 / Segunda Entrega**, o AUTO global V2 passa a existir como fluxo
separado e selecionável na UI. A descoberta global continua a reutilizar o
detetor comprovado 1.1.7 para encontrar as faces; depois cada face é refinada
automaticamente pelo RAW TIN V2.

Se o RAW TIN V2 falhar ou divergir de forma insegura numa face, essa face
mantém o par da baseline através de `BASELINE_1_1_7_FALLBACK`. Assim a
experimentação V2 não deve reduzir silenciosamente a cobertura já obtida pela
baseline.

## Comparação na mesma aplicação

Em FEATURE LINES existe:

- **Baseline 1.1.7**
- **V2 RAW TIN**

O V2 é desenhado com cores de comparação:
- CRISTA V2: verde;
- PÉ V2: magenta.

A baseline mantém amarelo/ciano. É possível aceitar linhas de um motor e depois
executar o outro na mesma face para comparar visualmente.

## Pipeline V2 Exp1

```text
PONTOS GROUND LOCAIS
        ↓
redução XY robusta
Z mediano por célula
        ↓
DELAUNAY TIN REAL DOS PONTOS
        ↓
normais + slope por triângulo
        ↓
seed = clique do utilizador
        ↓
REGION GROWING NA TIN
normal local + slope + edge length
        ↓
BOUNDARY DA FACE
        ↓
separar CREST / TOE
pela direção downhill
        ↓
ligar pequenos gaps
        ↓
KRUSKAL MINIMUM SPANNING TREE
        ↓
maior caminho contínuo
        ↓
estações ~1 m
        ↓
patches RAW dos dois lados
        ↓
robust plane A
robust plane B
        ↓
A ∩ B
        ↓
XYZ final
```

## Objetivo histórico do Exp1

A primeira experiência foi deliberadamente limitada à face clicada para provar
que o V2 conseguia simultaneamente:

1. ficar mais próximo da aresta física do que a linha raster;
2. eliminar spikes em V;
3. manter continuidade através de pequenas falhas;
4. produzir uma polyline CAD com ~1 vértice/m;
5. fornecer métricas objetivas de debug.

## Métricas guardadas

- `raw_points`
- `tin_points`
- `tin_triangles`
- `face_triangles`
- `crest_candidate_edges`
- `toe_candidate_edges`
- `face_slope_median_deg`
- `refine_ratio`
- `refined_stations`
- `fallback_stations`
- `plane_rmse_median`

Os eventos ficam em `PROJECT_DEBUG.log` como:

- `v2_raw_tin.backend_started`
- `v2_raw_tin.backend_completed`
- `v2_raw_tin.backend_failed`

## Primeiro teste recomendado

1. Abrir a mesma cloud usada nos testes 1.1.7.
2. Selecionar **Só Solo**.
3. FEATURE LINES → **Múltiplo**.
4. Motor → **Baseline 1.1.7**.
5. Picar o centro de uma face longa, aceitar a linha.
6. Motor → **V2 RAW TIN**.
7. Picar aproximadamente o mesmo local.
8. Comparar amarelo/ciano (baseline) com verde/magenta (V2).

Este procedimento continua útil para comparação local do **Exp1**. Para testar
a **Exp2**, selecionar **V2 RAW TIN** e executar **DETETAR CRISTA + PÉ**, que
agora chama o AUTO GLOBAL V2.

## Build local

O ficheiro `localbuild/talude_v1.json` desta branch está fixado em:

```text
v2-experimental-raw-tin-mst
```

Antes do primeiro build, confirmar no repositório local:

```powershell
git fetch origin
git switch v2-experimental-raw-tin-mst
git pull
```

Depois usar o Local Build Manager normalmente.

## Critério de promoção

Nada desta branch deve substituir ou modificar a baseline enquanto não passar:

- teste sintético;
- teste visual em faces limpas;
- teste em faces curvas;
- teste com Ground esparso;
- comparação quantitativa contra linha manual;
- ausência de spikes/loops/branches falsos.



## Primeira entrega obrigatória — 2026-09-27

Implementada em V2 sem alterar a baseline:

- coerência local neighbour↔neighbour para curvas e S;
- comprimentos 2D/3D acumulados;
- reason codes estáveis;
- quality score explícito;
- métricas de topologia/refinement;
- testes straight/curved/S;
- documentação de arquitetura e before/after.

Ver:

- `docs/V2_ARCHITECTURE.md`
- `docs/V2_FIRST_DELIVERY.md`


## Segunda entrega — AUTO GLOBAL V2 — 2026-09-27

Implementada sem alterar a baseline:

```text
GLOBAL DETECTOR 1.1.7
        ↓
FACE CANDIDATES
        ↓
ROI RAW GROUND AUTOMÁTICA
        ↓
RAW TIN V2 POR FACE
        ↓
CREST / TOE
        ↓
3D REFINEMENT
        ↓
REGRESSION GUARD
        ↓
V2 RESULT ou BASELINE FALLBACK
        ↓
RESULTADO GLOBAL
```

Inclui:

- processamento automático de todas as faces encontradas;
- seed e ROI calculadas automaticamente, sem cliques;
- recolha RAW Ground por streaming em LAS/LAZ;
- índice espacial para evitar testar todas as faces contra todos os pontos;
- reservoir limitado por face sem destruir precisão XYZ;
- RAW TIN + MST + refinamento local por face;
- preservação da geometria baseline quando a V2 falha;
- `reason_counts` e debug por face;
- progresso real;
- cancelamento cooperativo;
- export global DXF/GeoJSON/CSV;
- testes de integração e testes sintéticos.

Documentação detalhada:

- `docs/V2_SECOND_DELIVERY.md`

A terceira entrega será o **TILED ENGINE + HALO + stitching**. Não está
misturada nesta Exp2.


## Fases 3–5 — TILED + REFINEMENT + VECTOR DOCUMENT — 2026-09-27

Implementadas na branch experimental sem alterar a baseline:

### Fase 3

- core tiles;
- halo;
- spool RAW Ground em float64;
- uma passagem streaming pela cloud;
- RAW TIN por face × tile;
- clipping ao core;
- deduplicação dos overlaps;
- stitching por face;
- coverage guard;
- gap guard;
- fallback 1.1.7.

### Fase 4

- robust plane com support + inlier ratio;
- quality support-aware;
- smooth robusto apenas dos deltas de refinement;
- bridge de gaps curtos;
- métricas de support por CRISTA/PÉ.

### Fase 5

- `talude-vector-document/v1`;
- Engine Result → Vector Document → Features;
- LineStringZ;
- layers base CRISTA / PE_TALUDE;
- documento por run;
- documento ativo do projeto;
- API read-only do documento.

Ver:

- `docs/V2_PHASES_3_5.md`

As Fases 6–9 devem trabalhar sobre o Vector Document e não voltar a alterar o
motor geométrico sem uma razão técnica isolada e testada.
