# Talude Studio V1 — CRISTA + PÉ automático

Aplicação desktop dedicada à extração automática de **linhas 3D de CRISTA e PÉ DE TALUDE** a partir de nuvens LAS/LAZ/COPC.

## Interface V1.1

A interface simples em Tkinter foi substituída por uma workstation 3D baseada na shell comprovada do projeto **Cloud_to_lines**:

- PySide6 + QtWebEngine;
- Potree 1.8.2 para visualização out-of-core/LOD;
- EDL;
- RGB / Elevação / Classificação;
- seleção de classes LAS;
- overlay de linhas por cima do EDL com `THREE.Line2`;
- sidebar profissional;
- processamento em background;
- resultados CRISTA/PÉ visíveis diretamente sobre a nuvem.

A reutilização é feita a partir do repositório privado do mesmo proprietário:
`binopinto07-lang/Cloud_to_lines`.

O motor automático de taludes continua independente em `src/talude_v1`.

## Fluxo principal

```text
LAS / LAZ / COPC
        ↓
Potree viewer 3D
        ↓
Filtro por classificação
        ↓
DETETAR CRISTA + PÉ
        ↓
spacing AUTO
        ↓
grelha multiescala
        ↓
slope + persistence + hysteresis
        ↓
FACE_DETECTOR
        ↓
CRISTA + PÉ
        ↓
refinamento Z na nuvem original
        ↓
overlay 3D + DXF + GeoJSON + CSV + debug
```

## Cores no viewer

- **amarelo** — CRISTA;
- **ciano** — PÉ DE TALUDE.

## Execução em desenvolvimento

```bat
scripts\START_TALUDE_V1_GUI.bat
```

Na primeira execução são instaladas as dependências e descarregados Potree 1.8.2 + PotreeConverter 2.1.

## Build local

Não são necessários GitHub Actions.

O `Local Build Manager` usa `localbuild/talude_v1.json` e executa:

```text
requirements-dev.txt
        ↓
compileall
        ↓
pytest
        ↓
Potree + PotreeConverter
        ↓
PyInstaller + QtWebEngine
        ↓
self-test do EXE
        ↓
Talude_V1_Windows_x64.zip
```

Saída:

```text
builds/latest/Talude_V1_Windows_x64.zip
```

## Outputs do motor

Cada execução automática cria uma pasta em `exports/talude_auto_...` com:

- `talude_breaklines.dxf`;
- `talude_breaklines.geojson`;
- `talude_vertices.csv`;
- `talude_report.json`;
- `debug/01_slope.asc`;
- `debug/02_persistence.asc`;
- `debug/03_face_mask.asc`.

## Estado atual

A V1.1 implementa o primeiro **TERRAIN / FACE_FIRST engine**. O objetivo é obter CRISTA + PÉ automaticamente de forma funcional antes de acrescentar os motores TIN, PROFILE, PLANAR_3D e STRUCTURAL_EDGE.


## Clouds massivas / streaming

LAS e LAZ são processados **out-of-core**. O motor não usa `laspy.read()` no caminho de extração:

```text
LAS/LAZ
  ↓
header + sample de spacing
  ↓
leitura por chunks
  ↓
grelha coarse com orçamento de memória
  ↓
slope multiescala / FACE_DETECTOR
  ↓
CRISTA/PÉ aproximados
  ↓
segunda passagem por chunks
  ↓
refinamento local XYZ
```

A resolução da grelha AUTO pode ser aumentada automaticamente quando a extensão da cloud produzir demasiadas células; o valor efetivamente usado fica registado em `talude_report.json`.

Em grelhas grandes, os debug layers são guardados em `debug/terrain_debug.npz` para evitar centenas de MB de ASCII.


## V1.1.6 — face clicada + PAN

A deteção global mantém a base **1.1.2-feature-fix**. Melhorias posteriores que filtravam faces por largura/altura foram retiradas por reduzirem demasiado a deteção.

### Navegação CAD

- botão esquerdo + arrastar: ORBIT;
- botão direito + arrastar: PAN;
- botão do meio + arrastar: PAN;
- botão `PAN`: transforma temporariamente o botão esquerdo em PAN;
- roda: zoom;
- vistas rápidas: TOPO / FRENTE / TRÁS / ESQ / DIR / ISO.

Quando `Picar face automática` está armado, um clique curto seleciona a face; arrastar continua a rodar normalmente.

### Picar face automática

O clique deixou de usar um detector diferente. A zona clicada passa pelo mesmo pipeline do AUTO:

```text
pontos locais
  ↓
spacing / cell size
  ↓
slope multiescala
  ↓
persistence + hysteresis
  ↓
FACE_DETECTOR 1.1.2
  ↓
componente mais próximo do clique
  ↓
CRISTA + PÉ
  ↓
refinamento XYZ
```

Manual / Assistido / Múltiplo controlam apenas o tamanho da janela local analisada.

### Suavização

A suavização é pós-deteção e não altera quais as faces encontradas. O valor recomendado passou para `15`, podendo ser ajustado em **Parâmetros avançados → Suavização linha**.


## V1.1.7 — refinamento sem trocar o detector

Esta versão corrige duas regressões observadas nos testes reais da cloud de 241 M pontos:

- **AUTO global:** quando `Usar classes visíveis no motor` estava desligado, a UI enviava `[]`, o que acabava por processar todas as classes. O comportamento volta a ser **Solo / classe 2 por defeito**, como no resultado de 563–564 faces da base 1.1.2/1.1.5.
- **Face clicada:** o ProfileRequest estava a terminar por `soft_timeout` ainda com apenas alguns milhares de pontos e o espaçamento dessa amostra LOD fazia a grelha subir até **1.0 m**. Agora uma amostra pobre continua a carregar até ao limite duro e a grelha AUTO local é limitada a **0.35 m** quando o utilizador deixa Cell size = 0.

A suavização predefinida regressa a **11**, que preserva melhor curvas e extremos. A deteção de faces continua a ser a base 1.1.2; a suavização permanece exclusivamente pós-deteção.


## V2 Exp2 — AUTO GLOBAL RAW TIN

A branch `v2-experimental-raw-tin-mst` acrescenta um segundo AUTO global sem
alterar a baseline `baseline-1.1.7-refine`.

Na UI, o seletor **Motor da face** passa a controlar também
**DETETAR CRISTA + PÉ**:

```text
Baseline 1.1.7
  → AUTO comprovado 1.1.7

V2 RAW TIN
  → AUTO GLOBAL V2
  → descoberta de faces pela baseline
  → ROI RAW Ground automática
  → Delaunay TIN por face
  → local region growing
  → boundary CREST/TOE
  → Kruskal MST
  → refinamento 3D
  → fallback baseline quando V2 não é segura
```

A V2 global produz ainda:

- `debug/v2_faces.jsonl`;
- `debug/summary.json`;
- contadores de faces V2 bem sucedidas e fallbacks;
- reason codes por falha/rejeição;
- botão de cancelamento do processamento.

Ver `docs/V2_SECOND_DELIVERY.md`.
