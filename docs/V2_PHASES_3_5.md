# V2 — Fases 3, 4 e 5

Data: 2026-09-27  
Branch: `v2-experimental-raw-tin-mst`  
Baseline READ ONLY: `baseline-1.1.7-refine`  
Baseline SHA: `bfc21944eb337b57f2d8c31905cb7a0e85b3ed7f`

## Decisão

As Fases 3, 4 e 5 foram executadas como um bloco técnico único porque formam
uma cadeia direta:

```text
FASE 3
TILED ENGINE
    ↓
FASE 4
REFINEMENT ROBUSTO
    ↓
FASE 5
VECTOR DOCUMENT
```

As Fases 6–9 ficam sobre o Vector Document e não devem obrigar o motor RAW-TIN
a mudar novamente.

---

# Fase 3 — TILED ENGINE + HALO + STITCHING

## Problema resolvido

Uma cloud de centenas de milhões de pontos não deve ser tratada como uma única
TIN nem obrigar cada face a percorrer toda a cloud.

O caminho de produção LAS/LAZ passa agora por:

```text
GLOBAL DETECTOR 1.1.7
        ↓
faces candidatas
        ↓
grade espacial
        ↓
stream Ground uma vez
        ↓
CORE TILES em spool temporário
        ↓
TILE + HALO
        ↓
RAW TIN local
        ↓
fragmentos CRISTA/PÉ
        ↓
deduplicação de overlap
        ↓
STITCH por face
        ↓
regression guard
        ↓
V2 ou baseline fallback
```

## Core tile

Valor inicial:

`60 m`

Config:

`tile_size_m`

Cada ponto Ground é escrito uma única vez no seu core tile.

## Halo

Valor inicial:

`10 m`

Config:

`tile_halo_m`

Quando um tile é processado, são lidos também os core tiles vizinhos e é feito
clip para a bbox expandida pelo halo.

Isto evita:

- linhas cortadas exatamente na fronteira do tile;
- TIN sem contexto;
- falso fim de talude na grelha;
- perda da superfície TOP/FACE/BOTTOM junto à borda.

## Spool

O spool é temporário:

```text
debug/_tile_spool/
  tile_00000001.f64
  tile_00000002.f64
  ...
```

As coordenadas são mantidas em `float64`.

O spool é apagado no `finally`, incluindo quando existe erro/cancelamento.

## Fragmentos

Cada face baseline pode atravessar vários tiles.

É criada uma seed automática por:

`face × tile`

Cada tile resolve localmente:

- CRISTA;
- PÉ;
- quality;
- support;
- refinement.

A parte interior do core é guardada como fragmento.

## Stitching

Os fragmentos são projetados sobre a linha baseline correspondente apenas para
obter uma coordenada longitudinal estável.

Depois são:

1. orientados no mesmo sentido;
2. ordenados;
3. agrupados em bins longitudinais;
4. combinados dando mais peso ao fragmento de maior quality;
5. deduplicados nas zonas de overlap;
6. verificados quanto a gaps;
7. reamostrados para o station spacing V2.

## Guards

Um stitch é rejeitado se:

- cobertura < `tile_min_coverage_ratio`;
- existir gap espacial perigoso > `tile_stitch_gap_m`;
- faltar CRISTA ou PÉ;
- a linha final ficar inválida.

Nesse caso:

`BASELINE_1_1_7_FALLBACK`

## Debug

Novo ficheiro:

`debug/v2_tiles.jsonl`

Regista:

- tile_id;
- ix / iy;
- face_id;
- seed;
- pontos no tile+halo;
- pontos na ROI;
- estado;
- reason;
- métricas V2;
- número de fragmentos mantidos.

O `talude_report.json` passa também a incluir:

`tiled`

com:

- tile_size_m;
- halo_m;
- nx / ny;
- candidate_tiles;
- source_tiles_needed;
- tile_jobs;
- successful_faces;
- failed_faces;
- spool stats;
- elapsed_s.

---

# Fase 4 — SUPPORT-AWARE ROBUST REFINEMENT

## Objetivo

Evitar que um patch com poucos pontos ou um fit local fraco crie:

- V;
- spike;
- zig-zag;
- salto de vértice;
- quebra brusca entre estações.

## Robust plane

O fit local continua a usar planos:

`z = ax + by + c`

mas passa a devolver também:

- support;
- inlier ratio;
- RMSE.

Foram aumentadas as iterações robustas e mantido MAD para rejeitar outliers.

## Quality da estação

Cada estação refinada recebe peso a partir de:

```text
support count
+
inlier ratio
+
plane RMSE
```

Uma estação fraca não pode puxar com o mesmo peso uma estação bem suportada.

## Smooth de deltas

Não se suaviza a geometria toda indiscriminadamente.

É calculado:

`delta = ponto refinado - estação TIN`

e apenas esse delta é suavizado localmente.

Assim:

- a topologia detetada é preservada;
- curvas continuam curvas;
- a linha não é transformada numa spline artificial;
- spikes locais perdem influência.

## Short gap bridge

Runs curtos sem fit válido podem ser interpolados apenas quando ficam entre
duas estações refinadas confiáveis.

Config:

`refine_bridge_stations`

Default:

`2`

Não se fazem bridges longos.

## Métricas novas

Por linha:

- refined_stations;
- bridged_stations;
- fallback_stations;
- support_ratio;
- support_median;
- support_p10;
- plane_rmse_median;
- mean_shift_m;
- p95_shift_m;
- max_shift_m.

A quality de CRISTA e PÉ passa a usar o seu próprio `support_ratio`.

---

# Fase 5 — VECTOR DOCUMENT

## Objetivo

Separar definitivamente:

```text
ENGINE RESULT
      ↓
VECTOR DOCUMENT
      ↓
FEATURES
      ↓
LAYERS / EDITOR / SAVE / EXPORT
```

A UI futura deixa de ter de editar diretamente objetos temporários do motor.

## Schema

`talude-vector-document/v1`

Estrutura:

```text
document
├── document_id
├── revision
├── crs_wkt
├── source
├── layers
├── features
└── history
```

## Feature

Cada linha passa a ter:

- id único;
- layer_id;
- geometry LineStringZ;
- properties;
- visible;
- locked;
- selected;
- revision;
- created_at;
- updated_at.

## Layers base

Criadas:

- CRISTA
- PE_TALUDE

FACES e DEBUG entram na Fase 6 como layers adicionais do mesmo documento.

## Persistência

Cada execução cria:

`exports/<run>/vector_document.json`

e atualiza a cópia ativa do projeto:

`vectors/vector_document.json`

O `state.json` passa a conhecer:

- active_vector_document;
- active_vector_document_summary.

## API

Novos endpoints read-only nesta fase:

```text
GET /api/projects/{project_id}/vector-document
GET /api/projects/{project_id}/vector-document/summary
```

A escrita/edição transacional pertence às Fases 7–8.

---

# Não-regressão

Nenhuma alteração foi feita na branch:

`baseline-1.1.7-refine`

A V2 mantém:

```text
V2 segura
→ resultado V2

V2 insegura
→ BASELINE_1_1_7_FALLBACK
```

A Fase 3 não elimina esse princípio.

---

# Testes acrescentados

`tests/test_v2_phases_3_5.py`

Cobre:

- criação de vários tile jobs numa face longa;
- stitching com overlap;
- deduplicação;
- cobertura;
- robust plane com outliers;
- support/inlier ratio;
- smooth de spike;
- bridge curto;
- criação e roundtrip do Vector Document.

`tests/test_v2_integration_contract.py`

Cobre ligação end-to-end das Fases 3–5.

---

# Próximas fases

## Fase 6 — LAYERS

```text
CRISTA
PÉ
FACES
DEBUG
```

com visibilidade, seleção, lock e contadores.

## Fase 7 — EDITOR

- selecionar;
- mover vértice;
- inserir;
- apagar;
- apagar linha;
- linha manual;
- snap;
- lock;
- undo / redo.

## Fase 8 — PROJECT

- save;
- load;
- autosave;
- revisão;
- recovery.

## Fase 9 — EXPORT

- DXF 3D;
- SHP 3D;
- GPKG;
- atributos;
- export por layer;
- export apenas features visíveis/selecionadas.

Estas fases devem consumir o Vector Document criado na Fase 5, sem alterar o
motor geométrico.
