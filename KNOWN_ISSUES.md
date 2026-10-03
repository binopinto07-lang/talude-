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

## Limitações V3 por validar no Windows

- O pipeline original do classificador standalone ainda não suporta a Soalheira completa, até ser fornecida uma atualização com streaming/tiled real.
- A classificação foi ligada ao importador Potree, mas a integração ponta-a-ponta só pode ser confirmada com dependências completas no Windows.
- A implementação MDT via Rasterio lê e escreve tiles internamente, mas mantém dois grids NumPy densos; bloqueia automaticamente mais de 9 milhões de células.
- TALUDE_AUTO original usa processos QGIS apenas na parte DEM->contornos; o port independente reimplementa essas operações. A equivalência geométrica completa V0.2.3 necessita de ensaios sobre MDT real.
- O MDT da V3 é uma média Ground por célula observada, não uma reconstrução dos vazios por manto invertido.
