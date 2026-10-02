# V2 — Segunda Entrega: AUTO GLOBAL V2

Data: 2026-09-27  
Branch: `v2-experimental-raw-tin-mst`  
Baseline protegida: `baseline-1.1.7-refine`  
SHA baseline: `bfc21944eb337b57f2d8c31905cb7a0e85b3ed7f`

## Objetivo

Implementar o fluxo:

```text
LOAD
  ↓
GLOBAL DETECTION 1.1.7
  ↓
FACE CANDIDATES
  ↓
ROI RAW GROUND
  ↓
RAW TIN V2 POR FACE
  ↓
CREST / TOE
  ↓
3D REFINEMENT
  ↓
REGRESSION GUARD
  ↓
RESULTADO GLOBAL
```

sem remover o AUTO baseline existente.

## 1. Descoberta global

O detector 1.1.7 continua a ser usado como descoberta de regiões porque é o
comportamento real já validado visualmente.

O output baseline é guardado em:

```text
_baseline_candidates/
```

e serve de:

- lista inicial de faces;
- CRISTA/PÉ aproximados;
- referência de continuidade;
- fallback de segurança.

## 2. ROI automática por face

Cada FACE baseline gera automaticamente:

- par CRISTA/PÉ;
- centerline intermédia;
- largura mediana;
- largura P90;
- seed XYZ;
- corredor RAW Ground.

Não é necessário clicar manualmente nas faces.

## 3. Recolha RAW Ground em clouds grandes

Para LAS/LAZ:

```text
stream LAS/LAZ
    ↓
class filter / Ground
    ↓
spatial hash 20 m
    ↓
candidate face corridors
    ↓
distance to local centerline
    ↓
bounded priority reservoir
```

Não é feito um scan completo por cada face.

Existe apenas uma passagem de recolha ROI depois da descoberta global.

O reservoir por face é limitado e estatisticamente distribuído ao longo de
todo o stream, evitando favorecer apenas os primeiros chunks do LAS.

## 4. V2 local por todas as faces

Cada ROI é entregue a:

`extract_face_raw_tin()`

Pipeline:

```text
RAW ROI
 ↓
XY median reduction
 ↓
Delaunay
 ↓
local region growing
 ↓
boundary
 ↓
crest/toe classification
 ↓
gap links
 ↓
Kruskal MST
 ↓
~1 m stations
 ↓
robust local surface intersection
```

## 5. Regression Guard

A Segunda Entrega introduz uma regra explícita:

> V2 experimental não pode fazer desaparecer uma face que a baseline detetava.

Se V2 falhar numa face por:

- pouco Ground;
- TIN inválida;
- boundary insuficiente;
- linha curta;
- refinement inválido;
- divergência excessiva da região baseline;
- erro interno;

o resultado final dessa face recebe:

`BASELINE_1_1_7_FALLBACK`

Assim podemos experimentar a geometria V2 sem voltar ao padrão
"um passo em frente, dois para trás".

## 6. Validação de divergência

A linha V2 é comparada espacialmente com a região baseline correspondente.

São calculados:

- distância mediana;
- P95;
- distância máxima.

Uma linha V2 que salta silenciosamente para um talude vizinho é rejeitada e
substituída pelo fallback baseline.

Esta validação é um guard rail, não ground truth definitivo.

## 7. Progress

O job mostra etapas reais:

1. descoberta global;
2. construção das ROI;
3. stream RAW Ground;
4. face N / total;
5. número V2 bem sucedido;
6. número de fallbacks;
7. export final.

## 8. Cancel

Foi adicionada API cooperativa:

```text
POST /api/jobs/{job_id}/cancel
```

e botão:

`Cancelar processamento`

O cancel token é verificado entre:

- progresso da descoberta baseline;
- chunks da cloud;
- faces V2.

## 9. Debug

Outputs:

```text
debug/
  v2_faces.jsonl
  summary.json
```

Cada face regista, conforme o resultado:

- face_id;
- status;
- reason;
- ROI points;
- corridor radius;
- baseline width;
- agreement V2/baseline;
- V2 metrics;
- elapsed time.

O summary guarda:

- candidate_faces;
- v2_success_faces;
- baseline_fallback_faces;
- reason_counts;
- ROI statistics.

## 10. UI

O seletor existente passa a controlar também o AUTO global:

```text
Baseline 1.1.7
→ DETETAR CRISTA + PÉ
→ /api/talude/auto

V2 RAW TIN
→ DETETAR CRISTA + PÉ
→ /api/v2/talude/auto
```

A lista de resultados identifica:

- `V2`;
- `fallback`.

## 11. Export

O resultado final global continua compatível com o Studio:

- `talude_breaklines.geojson`;
- `talude_vertices.csv`;
- `talude_breaklines.dxf`;
- `talude_report.json`.

Todos os vértices permanecem XYZ.

## 12. Métricas principais do report

```text
faces_detected
baseline_faces_detected
candidate_faces
v2_success_faces
baseline_fallback_faces
unpaired_baseline_lines
crest_lines
toe_lines
reason_counts
roi.points_total
roi.points_selected
roi.roi_seen_total
roi.roi_kept_total
elapsed_s
```

## 13. Testes acrescentados

`tests/test_v2_global_auto.py`

Valida:

- criação automática de candidato a partir de CRISTA/PÉ;
- cálculo de largura e seed;
- preservação de linhas baseline sem par;
- reservoir bounded;
- utilização RAW TIN real num talude sintético.

`tests/test_v2_integration_contract.py`

Valida:

- endpoint global baseline permanece;
- endpoint global V2 existe;
- endpoint clicked-face baseline permanece;
- endpoint clicked-face V2 permanece;
- cancel API existe;
- UI escolhe endpoint pelo motor;
- fallback baseline existe no motor;
- debug e contadores V2 estão ligados.

## 14. O que NÃO foi feito nesta entrega

Ainda não é a Fase 3 TILED ENGINE completa.

A recolha ROI já é streaming + spatial hash, mas o motor global V2 final ainda
não implementa:

- tiles + halo independentes;
- stitching de linhas entre tiles;
- deduplicação de faces de tiles;
- resume de tile failed.

Isso fica para a Terceira Entrega, sem alterar esta Segunda Entrega estável.
