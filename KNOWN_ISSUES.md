# Known Issues — Talude Studio V3

## Confirmados no histórico R20

| ID | Falha | Causa / decisão V3 | Verificação |
|---|---|---|---|
| K-01 | `DLL load failed while importing QtWebEngineWidgets`: caminho muito comprido | Preservar build em `%LOCALAPPDATA%/LBM/TaludeStudioV3Build`. Testar EXE após extrair o ZIP em `C:\TALUDE_V3`. | Preflight + self-test Windows e abertura pós-extração (ainda pendente). |
| K-02 | DEV Portátil: `No module named pip` | Novo BAT usa `ensurepip` antes das dependências, não transforma primeira ausência de pip num erro silencioso. Builder tradicional continua o método principal. | Testar primeiro arranque no Windows (pendente). |
| K-03 | R20 deteta muitas linhas curtas e falha regiões da Soalheira | Não substituir motor R20. Criar TALUDE_AUTO/MDT como análise independente, sem declarar melhoria geométrica até comparar com 2D_TOTAL. | Auditoria SOALHEIRA R20, teste comparativo V3 pendente. |
| K-04 | 95 faces `REFINEMENT_FAILED`; `review_line_count=0` | Preservar diagnóstico e não converter falha em linha publicada. Nova saída TALUDE_AUTO tem `diagnostico.json` quando não há feições válidas. | `test_geotiff_raster_adapter_refuses_missing_observation`. |
| K-05 | Classificador standalone original retém XYZ em memória | Bloquear por defeito mais de 25 milhões de pontos; nunca declarar Soalheira 318M suportada sem versão streaming/tiled real. | `test_classify_guard_prevents_large_original_in_memory`. |
| K-06 | TALUDE_AUTO original importa QGIS | Adapter AST isolado para o núcleo geométrico original + port independente de operações raster. Não importa qgis no app. | `test_core_loads_without_qgis`. |
| K-07 | Source guard pode recompilar código antigo como se fosse V3 | Perfil `localbuild/talude_v3.json` exige `TALUDE_V3_MODULAR_2026_10_03_R1`. V2 conserva o guard antigo na sua branch. | `required_source_revision`, validar no Windows. |

| K-08 | 04/10/2026, primeira etapa V3: `[Errno 28] No space left on device`, ao obter `PySide6_Addons-6.9.2` (160,2 MB) | Espaço livre insuficiente no volume que suporta pip/TEMP/venv; não é conflito de versões. R2 adiciona preflight de todos os volumes relevantes (8 GiB para TESTAR, 12 GiB para BUILD), antes de `pip`. Elimina a atualização automática desnecessária de pip em cada pipeline. **Não apaga ficheiros automaticamente.** | `tests/test_v3_disk_preflight.py` e log real de 04/10; verificar no Windows se deteta a falta de espaço. |

| K-09 | 04/10/2026: TESTAR passou 113/114 testes mas `test_classify_guard_prevents_large_original_in_memory` falhou em Windows | O teste lia UTF-8 com `Path.read_text()` sem `encoding`. A codificação local do Windows alterava `não` para texto ilegível. **Motor de classificação íntegro**, falha exclusiva na leitura textual do teste. Corrigidas as cinco leituras do ficheiro de testes para `read_text(encoding='utf-8')`, sem alterar algoritmos nem `SOURCE GUARD`. | Contrato original reproduzido em CP1252; mesma API em UTF-8 corresponde ao texto esperado; repetir `TESTAR` em Windows. |

## Recuperação K-08

1. No PowerShell: `Get-PSDrive C` (ou consultar Armazenamento nas Definições).
2. Libertar cerca de **15 GB** no volume relevante. Verificar ficheiros pessoais e versões antigas antes de remover; **não eliminar a nuvem original, `%LOCALAPPDATA%\\LBM\\v` ou `%LOCALAPPDATA%\\LBM\\py312`**.
3. Para inspecionar a cache pip sem a eliminar: `py -3.12 -m pip cache info`; `py -3.12 -m pip cache purge` é opcional e obrigará a descarregar novamente os pacotes em futuros ambientes.
4. Descarregar a branch `v3-modular` atualizada e escolher o perfil V3 R2. O `SOURCE GUARD` inicial V2 é esperado apenas quando o perfil V2 está selecionado, e V3 R1 foi validado com sucesso na execução reportada.
5. Utilizar primeiro **TESTAR**, depois **BUILD + TESTES** quando houver espaço disponível.

## Limitações V3 por validar no Windows

- O pipeline original do classificador standalone ainda não suporta a Soalheira completa, até ser fornecida uma atualização com streaming/tiled real.
- A classificação foi ligada ao importador Potree, mas a integração ponta-a-ponta só pode ser confirmada com dependências completas no Windows.
- A implementação MDT via Rasterio lê e escreve tiles internamente, mas mantém dois grids NumPy densos; bloqueia automaticamente mais de 9 milhões de células.
- TALUDE_AUTO original usa processos QGIS apenas na parte DEM->contornos; o port independente reimplementa essas operações. A equivalência geométrica completa V0.2.3 necessita de ensaios sobre MDT real.
- O MDT da V3 é uma média Ground por célula observada, não uma reconstrução dos vazios por manto invertido.

## K-10 — Ground Standalone V1 bloqueia nuvens com mais de 25M pontos (04/10/2026)

- **Sintoma comprovado:** captura da V3 ao classificar a Soalheira: `RuntimeError: Classificador Standalone V1 usa XYZ em RAM; nuvem com mais de 25 milhões de pontos ... classify_file_streamed`. Esta proteção estava correta e a nuvem original não foi alterada.
- **Causa:** `classify_file()` retém XYZ de todos os pontos, apesar de ler LAS/LAZ em chunks; o motor existente não conseguia processar os ~318M pontos em memória limitada.
- **Correção R3:** o algoritmo externo `ALGORITM/CLASSIFY/classify_las_algorithm.py` mantém **integralmente** o conteúdo V1 original e acrescenta apenas a função `classify_file_streamed`. A implementação encontra-se no ficheiro independente `classify_streaming.py` e a cópia original exata em `classify_las_algorithm_ORIGINAL_V1.py`. O EXE V3/R2 existente já deteta a função, logo **pode receber os dois ficheiros .py na pasta externa sem recompilar**.
- **Método:** duas passagens pelo LAS/LAZ: extremos/contagem por célula num grid global; segunda passagem aplica a fórmula V1 medida e grava imediatamente os chunks, conservando os atributos e classe 7. Nunca sintetiza Ground não observado. Ponto original e EPSG:3763 protegidos.
- **Testes isolados locais:** 9 passaram, 1 LAS real ficou sem execução por indisponibilidade de laspy neste ambiente. Os testes originais Windows antes de R3: 117/117 passaram. **Não existe ainda validação da classificação completa da Soalheira**, nem garantia de qualidade topográfica equivalente ao R20.3.
- **Portabilidade:** falha autónoma do self-test do ZIP extraído permanece pendente. Não confundir com a classificação nem desativar o teste. A próxima verificação deverá imprimir o conteúdo de `TALUDE_V1_SELF_TEST.txt` antes de falhar.
