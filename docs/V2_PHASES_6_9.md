# V2 — Fases 6 a 9 + Builder protegido

Data: 2026-09-27  
Branch: `v2-experimental-raw-tin-mst`  
Baseline protegida: `baseline-1.1.7-refine`

## Objetivo deste bloco

Transformar o resultado do motor V2 num objeto de trabalho editável e exportável,
sem voltar a alterar a geometria base da deteção.

```text
BREAKLINE ENGINE
      ↓
VECTOR DOCUMENT
      ↓
FASE 6 — LAYERS
      ↓
FASE 7 — EDITOR
      ↓
FASE 8 — PROJECT / RECOVERY
      ↓
FASE 9 — EXPORT
```

---

## Fase 6 — Layers

O Vector Document passa a ter quatro layers base:

- `CRISTA`
- `PE_TALUDE`
- `FACES`
- `DEBUG`

O painel do Studio permite:

- ligar/desligar visibilidade;
- bloquear/desbloquear layer;
- filtrar features;
- ver contagem por layer;
- selecionar uma feature individual.

Documentos antigos da Fase 5 são migrados automaticamente ao abrir:
`FACES` e `DEBUG` são acrescentadas sem invalidar CRISTA/PÉ existentes.

---

## Fase 7 — Editor vetorial 3D

Ferramentas implementadas:

- selecionar linha;
- selecionar vértice;
- mover vértice por clique direto na point cloud;
- inserir vértice depois do vértice selecionado;
- apagar vértice;
- apagar linha;
- criar nova CRISTA manual;
- criar novo PÉ manual;
- lock por layer;
- undo;
- redo.

O clique de edição usa a própria interseção Potree com a point cloud.

Isto significa:

```text
Mover vértice
   ↓
clicar na cloud
   ↓
XYZ real do ponto
   ↓
LineStringZ atualizada
```

Os handles de vértices usam coordenadas locais relativas ao primeiro ponto,
evitando perda de precisão Float32 em coordenadas UTM grandes.

---

## Fase 8 — Project / save / recovery

O documento ativo continua em:

`vectors/vector_document.json`

Foi acrescentado:

`vectors/vector_document.autosave.json`

e histórico limitado:

`vectors/revisions/rev_XXXXXX.json`

Cada alteração usa controlo de revisão otimista.

Se a revisão esperada não coincidir:

`REVISION_CONFLICT`

e o documento não é silenciosamente sobrescrito.

Autosave é feito depois de alterações no editor.

Também existe recuperação da revisão anterior.

---

## Fase 9 — Export

Exportação diretamente do Vector Document para:

- DXF 3D;
- SHP PolyLineZ 3D;
- GPKG LineStringZ;
- GeoJSON 3D opcional.

Filtros:

- todas as features;
- apenas visíveis;
- apenas a feature selecionada.

### DXF

Mantém layers:

- CRISTA
- PE_TALUDE

e escreve POLYLINE 3D.

### SHP

Implementação própria e leve:

- .shp
- .shx
- .dbf
- .prj
- .cpg

Shape type:

`13 — PolyLineZ`

Não foi adicionada uma stack GIS pesada apenas para exportar SHP.

### GPKG

Criado com `sqlite3` da biblioteca standard e geometria GeoPackageBinary +
WKB LineStringZ.

Isto evita introduzir GDAL/Fiona/GeoPandas no executável e reduz fortemente
o risco do Windows builder.

---

# Builder Windows — alteração obrigatória

O erro observado na etapa PyInstaller era particularmente sensível a paths
Windows longos.

O build deixou de usar uma linha PyInstaller gigantesca diretamente na pasta
extraída do repositório.

Agora usa:

`scripts/build_windows.py`

e todo o trabalho pesado é executado em:

`%LOCALAPPDATA%\LBM\TaludeStudioBuild`

Isto reduz muito o comprimento dos paths de:

- PySide6;
- Qt WebEngine;
- QML;
- scipy;
- PyInstaller work files.

O Local Build Manager passou a ter também:

`builder-preflight`

antes do PyInstaller.

O preflight verifica imports reais de:

- NumPy;
- SciPy;
- laspy/lazrs;
- ezdxf;
- pyproj;
- FastAPI/Uvicorn;
- QtWebEngine;
- motores V1/V2;
- tiled engine;
- Vector Document;
- exportador;
- backend;
- desktop Studio.

E verifica assets:

- Talude Studio entry point;
- viewer;
- vector_editor.js;
- vendor Potree/PotreeConverter.

Só depois entra no PyInstaller.

O EXE, self-test e package também trabalham no short path. Apenas o ZIP final
é copiado para `builds/latest` / archive.

---

# Proteção anti-regressão

A branch baseline continua a não ser usada como área de desenvolvimento.

O bloco 6–9 trabalha **depois** do motor:

```text
motor V2 / fallback baseline
          ↓
Vector Document
          ↓
layers/editor/project/export
```

Portanto edição/exportação não deve alterar:

- FACE detector;
- RAW TIN;
- TILED/HALO;
- stitching;
- refinement;
- fallback 1.1.7.

---

# Testes adicionados

`tests/test_v2_phases_6_9.py`

Valida:

- quatro layers;
- save/revisions;
- conflict detection;
- recovery;
- DXF 3D;
- SHP PolyLineZ;
- GPKG;
- contratos do viewer/editor/API.

`tests/test_localbuild_studio_contract.py`

Valida:

- branch experimental;
- short-path builder;
- preflight;
- assets desktop;
- pipeline de self-test.

Além disso, os três JavaScript principais foram verificados sintaticamente:

- `app.js`
- `talude_auto.js`
- `vector_editor.js`

---

# Regra para o próximo teste real

Executar no Local Build Manager:

`BUILD + TESTES`

A pipeline deve passar por:

1. pytest;
2. vendor;
3. builder preflight;
4. clean short-path;
5. PyInstaller;
6. EXE check;
7. self-test;
8. package;
9. ZIP.

Se houver erro, o log deve indicar a etapa exata sem voltar a alterar o motor
geométrico para corrigir problemas de packaging/UI.
