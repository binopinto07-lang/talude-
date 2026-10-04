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

- CLASSIFY LAS R20.4 completo está integrado. O perfil de memória/desempenho ainda precisa de validação na Soalheira completa; otimizações futuras devem ser feitas no próprio CLASSIFY LAS.
- A classificação foi ligada ao importador Potree, mas a integração ponta-a-ponta só pode ser confirmada com dependências completas no Windows.
- A implementação MDT via Rasterio lê e escreve tiles internamente, mas mantém dois grids NumPy densos; bloqueia automaticamente mais de 9 milhões de células.
- TALUDE_AUTO original usa processos QGIS apenas na parte DEM->contornos; o port independente reimplementa essas operações. A equivalência geométrica completa V0.2.3 necessita de ensaios sobre MDT real.
- O MDT R20.4 usa Ground classe 2 e pode preencher apenas pequenas lacunas configuradas; OBSERVATION_STATE distingue Ground medido de interpolação raster.

## K-10 — SUPERADO: classificador Standalone V1 / limite 25M

O bloqueio do Standalone V1 e a extensão `classify_streaming.py` pertencem à
arquitetura anterior. Na branch `v3-classify-las-r20-4` esses ficheiros foram
removidos e substituídos por `ALGORITM/CLASSIFY_LAS`, snapshot do motor
completo LAS-CAFIISICA R20.4.

Isto elimina a divergência funcional entre o classificador simplificado do
Talude e o CLASSIFY LAS. Não significa que 318M pontos estejam já validados no
Windows: o motor completo conserva o seu próprio perfil computacional e exige
TESTAR/BUILD + TESTES e ensaio real antes de ser declarado pronto para produção.

