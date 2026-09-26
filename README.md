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
