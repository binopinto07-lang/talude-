# Talude Studio V2 Experimental — RAW TIN + MST

> Branch: `v2`
>
> Baseline protegida: commit `bfc21944eb337b57f2d8c31905cb7a0e85b3ed7f`
> (Talude Studio 1.1.7-refine).

## Regra principal

A baseline 1.1.7 não é alterada. O AUTO global continua a usar o motor que
produziu ~563 pares CRISTA/PÉ na cloud real. O V2 entra apenas no fluxo
**Picar face automática**, selecionável na UI.

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

## Objetivo do Exp1

Ainda não substituir o AUTO global. Primeiro provar numa face conhecida que o
V2 consegue simultaneamente:

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

O AUTO global não deve ser usado para julgar o V2 Exp1: ele continua
deliberadamente congelado na baseline.

## Build local

O ficheiro `localbuild/talude_v1.json` desta branch usa a branch curta:

```text
v2
```

Antes do primeiro build, confirmar no repositório local:

```powershell
git fetch origin
git switch v2
git pull
```

Depois usar o Local Build Manager normalmente.

## Critério de promoção

Nada desta branch deve substituir a baseline enquanto não passar:

- teste sintético;
- teste visual em faces limpas;
- teste em faces curvas;
- teste com Ground esparso;
- comparação quantitativa contra linha manual;
- ausência de spikes/loops/branches falsos.

