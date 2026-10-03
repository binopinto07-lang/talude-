# TALUDE STUDIO V3 — arquitetura modular

**Branch exclusiva** `v3-modular` criada a partir de `b3cda493...` (Talude Studio R20). V2 e baseline 1.1.7 ficam preservadas. V3 não é uma renomeação destrutiva da V2.

## Utilização

1. Descarregar o ZIP da branch `v3-modular` do GitHub e extrair numa pasta curta, por exemplo `C:\TALUDE_V3\`.
2. Iniciar `LocalBuildManager__TALUDE_STUDIO/START_LOCAL_BUILD_MANAGER.bat`.
3. Escolher **Talude Studio V3** (`localbuild/talude_v3.json`), a pasta raiz do repositório, e clicar TESTAR. BUILD + TESTES quando pretender pacote .exe.
4. Alternativamente, para executar código-fonte, duplo clique `INICIAR_TALUDE_DEV.bat` (precisa Python 3.12 preparado; auto instala as dependências em falta).
5. Fluxo: IMPORTAR -> CLASSIFICAR -> GERAR MDT -> DETETAR CRISTA+PÉ (motor R20) e TALUDE AUTO (MDT).

## Organização

- `ALGORITM/CLASSIFY/classify_las_algorithm.py`: original Standalone V1, byte-a-byte preservado.
- `ALGORITM/TALUDE_AUTO/TALUDE_AUTO.py`: original V0.2.3, byte-a-byte preservado.
- `studio/backend/v3_algorithms.py`: loaders e compatibilidade; procura `ALGORITM` externa junto da distribuição do EXE, ou `TALUDE_ALGORITM_DIR` explícito. Versão compatível é recarregada na próxima operação, sem recompilar.
- `studio/backend/v3_mdt.py`: MDT GeoTIFF classe 2 / nodata EPSG:3763.
- `studio/backend/v3_talude_auto.py`: adapter raster + núcleo geométrico do ficheiro original, sem QGIS.
- `studio/backend/v3_api.py`: rotas e jobs; reutiliza o R20 inalterado para o botão TALUDE STUDIO.
- `studio/viewer/v3.js` / `v3.css`: painel compacto com visibilidade real na viewport; o resto do DOM R20 permanece oculto mas não foi destruído.
- `localbuild/talude_v3.json`: perfil isolado Windows x64 para Local Build Manager, externa `ALGORITM` ao EXE.

## Informação de qualidade obrigatória

A V3 dispõe de integração modular implementada; isso **não significa** que os quatro botões já tenham passado ensaios end-to-end sobre a Soalheira 318M ou que uma versão EXE Windows tenha sido executada neste ambiente. O classificador Standalone V1 conserva uma limitação de memória; V3 protege a fonte com limite explícito de 25M pontos, até atualização do ficheiro externo por versão streaming. Não confundir testes sintéticos com resultados topográficos certificados.

## Substituição de algoritmos

Substituir ficheiro `.py` na pasta ALGORITM. O Adapter valida `API_VERSION=1.0` no classificador e `CORE_VERSION=0.1.3.1` para o núcleo geométrico TALUDE_AUTO. Se alterar o contrato, mostra erro explícito. A execução lê a versão atualizada na próxima operação. Não guardar ficheiros de terceiros no `src/` nem importar `qgis` no Studio.

## Resultados

`project/classified/`, `project/terrain/`, `project/exports/TALUDE_AUTO/`, `project/exports/talude_auto_v2_.../`, `project/logs/v3_...log`. Original nunca sobrescrito. TALUDE AUTO mantém camadas `TALUDE_TOPO` e `TALUDE_BASE` no DXF 3D.
