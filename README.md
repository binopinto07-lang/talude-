# Talude — BREAKLINE_ENGINE_V1

Primeira implementação independente para extrair automaticamente **CRISTA** e **PÉ DE TALUDE** em 3D a partir de nuvens de pontos.

## Objetivo V1

Pipeline funcional inicial:

`LAS/LAZ/XYZ → filtro Ground → espaçamento AUTO → grelha → slope multiescala → persistence → hysteresis → FACE_DETECTOR → boundaries CRISTA/PÉ → refinamento Z na cloud → DXF/GeoJSON/CSV`

A V1 não usa GitHub Actions. O GitHub serve apenas para versionamento; testes/build podem ser executados localmente com o Local Build Manager ou os scripts em `scripts/`.

## Instalação rápida (Windows)

```bat
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

## Executar

```bat
.venv\Scripts\python.exe -m talude_v1 levantamento.laz -o resultado
```

Por defeito, em LAS/LAZ usa `Classification == 2 (Ground)` quando essa classe existe em quantidade suficiente. Para processar todas as classes:

```bat
.venv\Scripts\python.exe -m talude_v1 levantamento.laz -o resultado --all-classes
```

## Saídas

- `talude_breaklines.dxf` — polylines 3D, layers `CRISTA` e `PE_TALUDE`
- `talude_breaklines.geojson` — linhas 3D + atributos de confiança
- `talude_vertices.csv` — XYZ por vértice
- `talude_report.json` — relatório completo da execução
- `talude_v1.log` — log
- `debug/01_slope.asc`
- `debug/02_persistence.asc`
- `debug/03_face_mask.asc`

## Estado da V1

Este primeiro motor é deliberadamente **FACE_FIRST**. Não tenta adivinhar a polyline diretamente na nuvem. Deteta a superfície inclinada persistente, separa o limite superior/inferior e só depois volta à cloud original para refinar Z.

A arquitetura já deixa espaço para os motores seguintes: TIN, PROFILE, PLANAR_3D, STRUCTURAL_EDGE, fusion, uncertainty e processamento COPC/tiled.


## Interface gráfica

No Windows pode arrancar diretamente com:

```bat
scripts\START_TALUDE_V1_GUI.bat
```

Selecione a nuvem, a pasta de saída e deixe Cell size / Slope LOW / Slope HIGH em `0` para seleção automática. O botão **EXTRAIR CRISTA + PÉ** executa o mesmo motor usado pela CLI.


## Local Build Manager — configuração correta

O projeto usa o mesmo padrão de build local que já funcionava no Cloud_to_lines:

`requirements.txt → requirements-dev.txt → compileall → pytest → PyInstaller → self-test → ZIP portátil`.

No Local Build Manager selecione **Talude V1 — Crista + Pé**. Se o gestor corrigido V0.1.3 for usado, ao selecionar uma pasta Git ele compara o `origin` com `repo_url` e muda automaticamente para o perfil correto.

Se o cabeçalho do log disser `LOCAL BUILD MANAGER | Cloud_to_lines` enquanto a pasta selecionada é `talude-`, o perfil errado está ativo e o pipeline deve ser interrompido.

O build validado é criado em:

```text
builds/latest/Talude_V1_Windows_x64.zip
```

O executável fica temporariamente em:

```text
builds/local_work/dist/Talude_V1/Talude_V1.exe
```

GitHub Actions não são necessários para este projeto.
