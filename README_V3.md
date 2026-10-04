# TALUDE STUDIO V3 — CLASSIFY LAS R20.4 modular

Branch: `v3-classify-las-r20-4`

Base preservada: `v3-modular`. Esta branch substitui apenas o classificador
simplificado anterior pelo módulo completo CLASSIFY LAS R20.4 e mantém os
motores de deteção de taludes separados.

## Fluxo

```text
IMPORTAR LAS/LAZ
  -> CLASSIFY LAS R20.4
  -> nuvem classificada
  -> GERAR MDT R20.4
  -> MDT + OBSERVATION_STATE
  -> DETETAR CRISTA + PÉ (Talude Studio)
  -> TALUDE AUTO (MDT)
```

## CLASSIFY LAS R20.4

`ALGORITM/CLASSIFY_LAS/` contém um snapshot vendorizado do núcleo
LAS-CAFIISICA R20.4:

- Adaptive PTD;
- dense spatial evidence;
- inverted Ground mantle;
- roof/canopy/elevated-object veto;
- breakline-safe measured Ground continuity;
- Ground export/classification;
- MDT com proveniência observado/interpolado.

P1, L3/LiDAR e fontes desconhecidas executam **o mesmo pipeline geométrico**.
Não existe seleção de sensor que mude de algoritmo. Estrutura multi-return é
evidência opcional apenas quando a população completa da nuvem a suporta.

O classificador simplificado V1 e `classify_streaming.py` foram removidos.

## Contrato externo

`ALGORITM/CLASSIFY_LAS/portable_api.py`

- `API_VERSION = "2.0"`;
- `inspect_source(...)`;
- `classify_file(...)`;
- `create_mdt_from_classified(...)`.

`studio/backend/v3_algorithms.py` apenas carrega/valida o módulo.
`studio/backend/v3_api.py` apenas orquestra jobs, publicação e Potree.

## MDT

O MDT é produzido a partir da classe 2 final do CLASSIFY LAS.

`OBSERVATION_STATE`:
- 0 = NO_GROUND_OBSERVATION;
- 1 = MEASURED_GROUND;
- 2 = INTERPOLATED_MDT.

Estado 2 é reconstrução raster e nunca é promovido a ponto LAS Ground medido.

## Organização

- `ALGORITM/CLASSIFY_LAS/` — CLASSIFY LAS R20.4 completo e substituível;
- `ALGORITM/TALUDE_AUTO/TALUDE_AUTO.py` — algoritmo independente;
- `studio/backend/v3_algorithms.py` — contratos/loader;
- `studio/backend/v3_api.py` — jobs;
- `studio/backend/v3_talude_auto.py` — adapter TALUDE_AUTO;
- `studio/viewer/v3.js` — painel V3;
- `localbuild/talude_v3.json` — Local Build Manager.

## Atualização futura do CLASSIFY LAS

Uma versão futura R21/R22 deve substituir a pasta
`ALGORITM/CLASSIFY_LAS` mantendo o contrato API 2.x compatível. O código de
deteção CRISTA/PÉ não deve incorporar cópias da lógica Ground.

## Estado de validação

A integração desta branch é **experimental até executar TESTAR/BUILD + TESTES
no Windows e ensaios de campo**. A presença do código e testes de contrato não
prova qualidade topográfica.

O módulo vendorizado mantém o perfil computacional do CLASSIFY LAS completo.
Não voltar ao classificador V1 simplificado apenas para reduzir memória; futuras
otimizações de streaming/tiled devem ser feitas no próprio CLASSIFY LAS e depois
sincronizadas para este módulo.

CRS de produção: **EPSG:3763 — ETRS89 / Portugal TM06**.
