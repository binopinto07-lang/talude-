# TALUDE STUDIO V3 — arquitetura modular

**Branch exclusiva** `v3-modular` criada a partir de `b3cda493...` (Talude Studio R20). V2 e baseline 1.1.7 ficam preservadas. V3 não é uma renomeação destrutiva da V2.

**R2 (04/10/2026):** o Local Builder verifica espaço livre antes do pip (8 GiB TESTAR; 12 GiB BUILD + TESTES); `pip` deixou de ser atualizado automaticamente em todas as execuções. Consulte `KNOWN_ISSUES.md` K-08 se o log indicar `[Errno 28]`.

**Correção UTF-8 para Windows (04/10/2026):** após o primeiro TESTAR reportar 113/114 testes aprovados, todas as leituras do ficheiro de testes V3 passaram a declarar `encoding='utf-8'` para não depender da codificação local do Windows. A instalação de dependências já tinha terminado com sucesso. Esta correção não altera o source guard R2 nem o comportamento dos algoritmos.

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

## R3 — Classificação Ground por streaming (04/10/2026)

Quando `CLASSIFICAR NUVEM` recebe mais de 25 milhões de pontos, o executável utiliza automaticamente `classify_file_streamed` se a função existir no ficheiro externo, sem alterar o motor R20. A V3 R3 inclui essa função como ligação fina ao módulo independente `ALGORITM/CLASSIFY/classify_streaming.py`. O original V1 foi preservado em `classify_las_algorithm_ORIGINAL_V1.py` (SHA-256 no `LEIA_PRIMEIRO` do patch).

Processamento: primeira passagem LAS/LAZ estima suporte por células globalmente; segunda escreve LAS/LAZ classificado em blocos mantendo XYZ/RGB/intensidade/ordem/CRS, preservando class 7. A **qualidade geométrica real** na Soalheira ainda precisa de ser testada; a implementação não replica o manto nem recupera terreno inexistente. A versão compilada R2 pode ser atualizada apenas substituindo os ficheiros externos na distribuição.


## CLASSIFY LAS R20.4 — módulo completo

Esta branch remove o antigo classificador simplificado de `ALGORITM/CLASSIFY`
e usa `ALGORITM/CLASSIFY_LAS`, um snapshot vendorizado do motor completo
LAS-CAFIISICA R20.4. P1, L3 e outras LAS/LAZ usam o mesmo pipeline Ground.
O módulo também gera o MDT e o mapa `OBSERVATION_STATE`, separando Ground
medido de interpolação raster. O motor de CRISTA/PÉ permanece independente.
