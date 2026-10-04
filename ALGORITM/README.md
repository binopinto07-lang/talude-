# ALGORITM — módulos externos V3

## CLASSIFY_LAS

O classificador simplificado V1/streaming anterior foi removido desta branch.

`CLASSIFY_LAS/` contém agora um snapshot vendorizado do motor completo
**LAS-CAFIISICA R20.4 Universal Ground + MDT**, com contrato externo
`API_VERSION = "2.0"`.

P1, L3/LiDAR e outras nuvens LAS/LAZ percorrem o mesmo pipeline geométrico.
Não existe escolha de sensor para selecionar outro algoritmo.

Entrada pública:
- `portable_api.py::inspect_source`
- `portable_api.py::classify_file`
- `portable_api.py::create_mdt_from_classified`

Para atualizar futuramente o CLASSIFY LAS, substituir a pasta
`ALGORITM/CLASSIFY_LAS` por uma versão compatível do módulo, sem alterar o
motor de CRISTA/PÉ do Talude Studio.

## TALUDE_AUTO

`TALUDE_AUTO/TALUDE_AUTO.py` permanece separado. O adapter extrai apenas o
núcleo geométrico QGIS-free já definido pelo projeto.

A aplicação não deve copiar lógica do CLASSIFY LAS para `studio/backend`:
`studio` apenas orquestra o módulo externo.
