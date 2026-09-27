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


## V1.1.8 — TIN EDGE + geometria topográfica

A deteção das faces continua congelada na base 1.1.2. Esta versão altera apenas a construção geométrica de CRISTA/PÉ.

### Porque a TIN ajuda

Uma crista ou pé de talude é uma **quebra de declive**. Numa superfície triangulada essa quebra aparece como uma mudança brusca da normal entre triângulos vizinhos. A V1.1.8 cria uma TIN 2.5D implícita sobre a grelha do terreno e calcula um `tin_break_score` a partir do ângulo entre normais adjacentes.

Não é criada uma Delaunay global com os 241 milhões de pontos: isso seria pesado e desnecessário. O LAS Solo continua a ser agregado na grelha de terreno e essa superfície é triangulada matematicamente em dois triângulos por célula.

### Nova geometria

1. FACE_DETECTOR 1.1.2 identifica a mesma máscara de talude.
2. A crista e o pé são retirados do **boundary da própria face**, classificados com o gradiente local. Isto permite acompanhar faces curvas sem depender de uma única direção média.
3. Cada linha é deslocada transversalmente até à quebra TIN mais forte próxima, respeitando o sinal de mudança de declive para não trocar CRISTA e PÉ.
4. A linha é suavizada apenas depois do snap.
5. Os vértices são reamostrados por distância. O valor predefinido é **1,00 m**, ou aproximadamente um vértice por metro.
6. O relatório guarda `geometry_source`, `raw_vertex_count`, `final_vertex_count`, `tin_mean_snap_m` e `tin_max_snap_m`.

O objetivo não é aumentar o número de taludes, mas colocar as linhas sobre a aresta topográfica e produzir polylines CAD mais limpas e com muito menos vértices.


## V1.1.9 — Ground rebuild + anti-V

Esta versão responde a dois problemas observados na cloud real:

1. segmentos em **V / bicos** nas linhas finais;
2. taludes interrompidos onde a classe **Ground** tem pouca densidade ou pequenas falhas.

### Reconstrução controlada do Ground

O motor deixa de tratar cada célula Ground vazia como um corte definitivo. Para pequenas falhas internas:

- mede a distância ao Ground real;
- interpola apenas entre suportes reais dos dois lados;
- por defeito reconstrói até **1,50 m**;
- grandes vazios continuam excluídos do FACE_DETECTOR;
- células reconstruídas podem **ligar** uma face, mas não podem iniciar sozinhas uma nova face forte.

Isto reduz linhas partidas sem inventar terreno em zonas sem suporte.

### Anti-V

Os picos em V tinham duas origens principais:

- o boundary da face podia entrar pelos lados do talude;
- o snap TIN podia saltar entre duas quebras vizinhas em vértices consecutivos.

A V1.1.9:

- rejeita boundary aproximadamente paralelo ao downhill;
- reforça continuidade do offset transversal durante o snap;
- aplica median filtering aos offsets;
- limita mudanças bruscas de offset;
- remove spikes isolados apenas quando a direção antes/depois continua praticamente igual.

### Debug novo

- `05_ground_analysis_valid.asc`
- `06_ground_support_distance_m.asc`
- `ground_real_cells`
- `ground_analysis_cells`
- `ground_reconstructed_cells`

O detector de base continua a ser o 1.1.2; estas alterações atuam na reconstrução do terreno e na geometria/continuidade final.
