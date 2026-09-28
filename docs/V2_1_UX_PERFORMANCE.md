# Talude Studio V2.1 — UX + Performance

Data: 2026-09-28  
Branch: `v2-experimental-raw-tin-mst`  
Baseline protegida: `baseline-1.1.7-refine`

## Objetivo

Esta entrega não substitui o detector protegido. Trabalha em três áreas:

1. gestão de layers semelhante ao QGIS;
2. navegação 3D semelhante ao Agisoft + vistas CAD;
3. reduzir o custo do AUTO GLOBAL V2 em nuvens muito grandes.

## Evidência do caso real usado para orientar performance

Dataset de trabalho:

- 241 149 524 pontos;
- LAS 1.2;
- escala XYZ 0.0001;
- ETRS89 / Portugal TM06 quando o CRS é resolvido pelo projeto.

Execução V2 observada:

- 563 faces candidatas;
- 307 faces RAW-TIN aceites;
- 256 fallback por MERGE_FAILED;
- 1126 linhas finais;
- 4407.13 s (~73.5 min).

Isto mostra que o problema dominante não é apenas ler o LAS: o modo preciso
executava RAW-TIN por centenas de faces/tiles mesmo quando muitas acabavam em
fallback.

## Layers estilo QGIS

O painel principal de CAMADAS passa a ser uma árvore agrupada:

```text
NUVEM DE PONTOS
  ☑ cloud0                         241M pts

BREAKLINES
  ☑ ─ CRISTA                       563
  ☑ ─ PÉ DE TALUDE                 563

ANÁLISE / SUPORTE
  ☐ ▰ FACES
  ☐ ┄ DEBUG
```

Cada layer vetorial tem:

- visibilidade;
- layer ativa;
- contador;
- lock/unlock;
- SOLO.

Toolbar:

- mostrar todas;
- ocultar todas;
- solo da ativa;
- atualizar;
- guardar;
- undo;
- redo.

A nuvem é tratada como layer somente leitura e pode ser ocultada/enquadrada.

## Navegação Agisoft

Perfil AGISOFT é o padrão:

- arrastar esquerdo: orbit;
- botão direito: pan;
- botão do meio: pan;
- Shift + esquerdo: pan;
- roda: zoom;
- duplo clique sobre a nuvem: novo pivot/foco;
- F: enquadrar.

Existe alternância AGISOFT / CAD sem alterar os dados do projeto.

## ViewCube CAD

Novo cubo no canto superior direito:

- TOP;
- BOTTOM;
- FRONT;
- BACK;
- LEFT;
- RIGHT;
- ISO.

Atalhos:

- Alt+1 TOP;
- Alt+2 FRONT;
- Alt+3 BACK;
- Alt+4 LEFT;
- Alt+5 RIGHT;
- Alt+6 ISO.

## AUTO V2 — perfis de processamento

### FAST

```text
Baseline global 1.1.7
        ↓
CRISTA + PÉ
        ↓
Vector Document
```

Não executa uma segunda passagem RAW-TIN sobre a cloud. Serve para obter um
resultado global rapidamente mantendo a geometria protegida.

### BALANCED — padrão

```text
Baseline global
    ↓
563 faces
    ↓
ranking de coerência
    ↓
RAW-TIN apenas nas faces prioritárias
    ↓
restantes = baseline segura
```

Em clouds >=150M pontos o limite inicial é 120 faces RAW-TIN. A seleção favorece
faces com largura coerente e dimensão adequada ao refinamento.

Não se perde nenhuma face: uma face não refinada continua no resultado através
da baseline.

### PRECISE

Preserva o comportamento anterior:

- tenta RAW-TIN em todas as faces;
- tiled + halo;
- stitching;
- fallback protegido.

É o modo mais lento e deve ser usado quando se pretende benchmark/qualidade
máxima e o tempo de execução é aceitável.

## Separação entre fallback técnico e desempenho

O relatório passa a distinguir:

- `v2_attempted_faces`;
- `v2_success_faces`;
- `performance_skipped_faces`;
- `baseline_fallback_faces`.

Assim uma face mantida na baseline por opção de velocidade não aparece como se
o RAW-TIN tivesse falhado.

## Face clicada

Se a face clicada com V2 falhar por condições locais (por exemplo,
`FACE_TOO_SMALL`), o Studio tenta automaticamente a mesma amostra com o motor
baseline protegido.

O operador deixa de ficar sem linha apenas porque a experiência RAW-TIN rejeitou
uma face local.

## Espaçamento de vértices

A UI expõe `Espaçamento vértices (m)`, default 1.0 m.

Este valor controla `station_spacing_m` do V2 e permite obter linhas finais
com vértices mais espaçados em vez de serrilhado excessivo.

## Proteções

Esta entrega não altera:

- branch `baseline-1.1.7-refine`;
- detector V1 protegido;
- DXF/SHP/GPKG;
- Vector Document;
- short-path Windows builder.

Não adiciona novas dependências ao executável.
