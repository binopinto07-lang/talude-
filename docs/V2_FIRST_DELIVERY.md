# V2 — Primeira Entrega Obrigatória

Data: 2026-09-27  
Branch de desenvolvimento: `v2-experimental-raw-tin-mst`

## A. Mapa da arquitetura atual

Ver `docs/V2_ARCHITECTURE.md`.

## B. SHA baseline 1.1.7

```text
branch: baseline-1.1.7-refine
sha:    bfc21944eb337b57f2d8c31905cb7a0e85b3ed7f
```

A comparação Git confirma que a V2 deriva desse SHA e a baseline não recebeu
commits V2.

## C. Lista de bugs / limitações V2 identificados

### Corrigidos nesta entrega

1. **Gradient coherence global inadequada para curvas/S**
   - V2 passa a medir coerência vizinho↔vizinho na TIN.
   - Vetores de extremos distantes não são usados como veto global.

2. **Comprimento ambíguo**
   - V2 passa a expor `length_2d_m` e `length_3d_m`.
   - ambos são acumulados segmento a segmento.

3. **Falhas sem reason code estável**
   - introduzido `V2Reason` + `V2DetectionError`.

4. **Debug insuficiente**
   - adicionadas métricas de coerência, MST, gap links, comprimentos, RMSE e shift.

5. **Confidence apresentado como se fosse probabilidade**
   - V2 passa a expor `quality_score`.
   - `confidence` fica temporariamente apenas como alias de compatibilidade da UI.

### Ainda abertos

1. AUTO global ainda é baseline 1.1.7; V2 RAW-TIN atua numa ROI clicada.
2. V2 ainda não processa cloud completa por tiles + halo.
3. Ground source ainda não possui modo híbrido/reconstruído completo.
4. gap linking ainda precisa custo multi-termo (ângulo/Z/curvatura/suporte/face).
5. longest path ainda escolhe uma linha principal por boundary.
6. ainda falta anti-V/backtracking explícito após a topologia.
7. fitting robusto atual usa rejeição MAD iterativa; ainda falta comparar RANSAC/Huber/IRLS.
8. transições suaves ainda precisam profile/curvature refinement.
9. não existe Vector Document/layers editáveis.
10. não existe undo/redo, lock, manual line ou persistência completa.
11. não existe export SHP LineStringZ/GPKG no modelo V2.
12. versão ainda deve ser centralizada numa source of truth numa fase posterior.

## D. Correção gradient coherence

### Antes

A filosofia antiga podia resumir direções de uma face inteira numa média única.
Numa face curva:

```text
↘ ↓ ↙
```

ou numa face S, direções distantes podem cancelar-se.

### Agora V2

`_local_face_coherence()` usa apenas pares de triângulos TIN adjacentes.

Métricas:

- `local_normal_coherence`;
- `local_direction_median_deg`;
- `local_direction_p95_deg`;
- `local_pairs`.

O region growing V2 também continua neighbour-to-neighbour.

## E. Correção polyline length

Nova função:

`_polyline_lengths()`

Saídas:

- `length_2d_m`;
- `length_3d_m`.

`length_m` permanece alias 2D para compatibilidade.

Existe teste em que os extremos estão próximos mas a linha percorre 19 m,
garantindo que não é usada a chord distance.

## F. Reason codes

Implementados em `src/talude_v2/reasons.py`.

Erros esperados deixam de ser apenas texto livre.

O backend V2 regista `status` e `reason` no log.

## G. Debug metrics

O resultado V2 contém agora `metrics` com:

```text
raw_points
tin_points
tin_triangles
face_triangles
face_slope_median_deg
local_normal_coherence
local_direction_median_deg
local_direction_p95_deg
crest_candidate_edges
toe_candidate_edges
crest_mst_edges
toe_mst_edges
crest_gap_links
toe_gap_links
crest/toe length 2D
crest/toe length 3D
refined_stations
fallback_stations
plane_rmse_median
mean_shift_m
p95_shift_m
max_shift_m
```

## H. Testes straight / curved / S

Criado `tests/test_v2_first_delivery.py`.

Inclui regressões explícitas para:

- straight;
- curved;
- S-curve;
- comprimento acumulado;
- reason codes;
- quality/debug metrics.

O teste S cria direções cujo vetor médio global se anula, mas exige que a
coerência local continue válida.

## I. Before / After

| Tema | BEFORE | AFTER |
|---|---|---|
| Baseline 1.1.7 | funcional | funcional / intocada |
| Coerência curva | risco por direção global | local neighbour↔neighbour |
| S-curve | vetores distantes podem cancelar | sem veto global |
| Length | apenas campo genérico | 2D + 3D acumulados |
| Rejeição | ValueError textual | reason code estável |
| MST debug | pouco visível | edges/gaps/path contabilizados |
| Refinement | refined/fallback/RMSE | + mean/P95/max shift |
| Confidence | nome ambíguo | quality_score + alias compatível |

## Critério desta entrega

Esta entrega **não promove o V2 a AUTO global**. O passo seguinte só deve
começar depois de os testes passarem e de a comparação visual RAW-TIN numa face
real não mostrar regressão geométrica.
