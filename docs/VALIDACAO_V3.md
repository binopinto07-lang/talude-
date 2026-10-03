# Validação V3 — registo honesto

## Testes executados neste ambiente

- Original TALUDE_AUTO e classificador copiados byte-a-byte para ALGORITM.
- `python -m compileall`: sintaxe dos módulos novos.
- `pytest -q tests/test_v3_modular.py`: 13 testes unitários, incluindo classificação de áreas Ground simuladas e exportação positiva de 1 CRISTA + 1 PÉ para DXF 3D em MDT sintético.
- `node --check studio/viewer/v3.js`: sintaxe JavaScript.

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
