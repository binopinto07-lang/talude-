# Validação V3 — registo honesto

## Testes executados neste ambiente

- Original TALUDE_AUTO e classificador copiados byte-a-byte para ALGORITM.
- `python -m compileall`: sintaxe dos módulos novos.
- `pytest -q tests/test_v3_modular.py`: 13 testes unitários, incluindo classificação de áreas Ground simuladas e exportação positiva de 1 CRISTA + 1 PÉ para DXF 3D em MDT sintético.
- `node --check studio/viewer/v3.js`: sintaxe JavaScript.

## Erro real recebido em 04/10/2026

- O Local Builder carregou o perfil V3 e aprovou `SOURCE GUARD: TALUDE_V3_MODULAR_2026_10_03_R1`.
- A primeira etapa falhou em 7,8 s: `[Errno 28] No space left on device`, quando o `pip` preparava `PySide6_Addons-6.9.2` de 160,2 MB.
- R2 adicionou uma verificação preventiva de espaço livre sem tentar eliminar ficheiros do utilizador. Antes de executar TESTAR/BUILD no Windows, é necessário libertar espaço no volume indicado.
- Testes isolados de regressão sobre falta de espaço passaram no ambiente de desenvolvimento; novo preflight Windows pendente.

## Correção de teste UTF-8 (04/10/2026)

- No novo log R1, as dependências instalaram em 44,9 s, `compileall` terminou OK e o pytest reportou **113 passed, 1 failed**.
- A falha do `test_classify_guard_prevents_large_original_in_memory` foi reproduzida lendo `v3_api.py` (UTF-8) como CP1252; a frase com `não` deixou de corresponder. Com `encoding='utf-8'`, a proteção de 25M e a mensagem foram encontradas.
- O teste V3 foi corrigido em todas as cinco chamadas `read_text()`; não se alteraram os motores, ALGORITM, Builder ou source guard. Resultado dos 114 testes no Windows ainda por recolher.

## Por validar no Windows 10/11 com Local Builder

1. Primeiro arranque com Python 3.12/PySide6 QtWebEngine e PotreeConverter.
2. Importação da nuvem original e da classificada.
3. Classificação real num recorte até 25M pontos (original Standalone V1, sem alterar).
4. Formação MDT com Ground real EPSG:3763; validar nodata, raster e sombreado.
5. Execução da deteção R20 sobre a nuvem classificada.
6. TALUDE_AUTO sobre o MDT produzido, verificar classes e DXF.
7. Vista simultânea original/classificada/MDT/linhas com checkboxes.
8. EXE compilado, extraído numa pasta diferente, ALGORITM externo atualizado, testado novamente.
9. Soalheira 318.154.588 pontos não pode classificar integralmente com classificador V1 em RAM. Exige versão compatível `classify_file_streamed` ou nova versão do algoritmo independente.

NENHUMA das verificações Windows acima deve ser marcada como aprovada sem log e captura reais.

## R3 — Extensão streaming (04/10/2026)

- Protótipo real da lógica com numpy/scipy e leitor/escritor LAS simulado: **9 passaram e 1 teste LAS real ignorado**, porque `laspy` não está instalado neste ambiente.
- Testes: classes iguais ao V1 em nuvem sintética, dimensões originais, classe 7, leitura em duas passagens, grid limitado, cancelamento com remoção parcial, incompatibilidade de CRS, discrepância da contagem LAS, wrapper detetável pelo carregador do EXE e original binariamente idêntico.
- Teste adicional com LAS real EPSG:3763 preparado para correr no teu Windows (laspy instalado); depois medir separadamente qualidade do recorte e Soalheira completa.
- A falha anterior no self-test depois de extrair o ZIP ainda não foi diagnosticada: exigir relatório de cada componente e não declarar portabilidade concluída.
