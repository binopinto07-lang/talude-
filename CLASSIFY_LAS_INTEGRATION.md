# TALUDE STUDEO — integração CLASSIFY LAS R20.4

## Estado

EXPERIMENTAL. Branch: `classify-las-r20-4-module`.

O `main` não foi alterado.

## Alteração

O classificador Ground simplificado/temporário deixa de ser a autoridade de terreno.
O módulo completo do CLASSIFY LAS foi incorporado em:

`ALGORITM/CLASSIFY_LAS/`

A origem é `LAS-CAFIISICA:r20-4-universal-mdt`.

## Pipeline

`LAS/LAZ original -> CLASSIFY LAS R20.4 -> CLASSIFIED -> Ground medido -> MDT -> TALUDE`

P1, L3 e outras nuvens usam o mesmo pipeline geométrico. Não existe escolha de
algoritmo por sensor. Retornos LiDAR são apenas evidência opcional quando informativos.

O MDT distingue:
- 0 NO_GROUND_OBSERVATION;
- 1 MEASURED_GROUND;
- 2 INTERPOLATED_MDT.

A interpolação do MDT nunca é escrita como ponto Ground medido.

## TALUDE

O botão **CLASSIFICAR GROUND + CRIAR MDT** executa o módulo em background.

O AUTO TALUDE passa a exigir uma classificação CLASSIFY LAS válida e lê classe 2
da nuvem `*_CLASSIFIED_R20_4.laz`. A reconstrução Ground local de 1,50 m foi
retirada da UI e desativada (`max_ground_gap_m=0.0`).

Os algoritmos CRISTA/PÉ continuam separados; não foram substituídos pelo classificador.

## Atualizações futuras

Quando o CLASSIFY LAS evoluir (R21/R22), atualizar o conteúdo de
`ALGORITM/CLASSIFY_LAS/las_classifier` e manter o contrato de
`ALGORITM/CLASSIFY_LAS/api.py`.

## Validação obrigatória

1. LocalBuildManager -> TEST.
2. Confirmar compileall de `ALGORITM`, `src`, `studio` e `tests`.
3. Confirmar pytest completo.
4. Testar P1 real.
5. Testar L3 real.
6. Confirmar GeoTIFF EPSG:3763.
7. Confirmar que AUTO TALUDE usa a nuvem classificada, não a original.
8. Só depois executar BUILD/EXE.
