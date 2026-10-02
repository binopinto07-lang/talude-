# Componentes incluídos e relação com BINO_APOIO

O DEV Portátil executa **todo o código-fonte Talude existente** (`src/talude_v1`, `src/talude_v2`, `core`, `studio`) tal como está na branch. A melhoria de R19/R20 é transportada com o resto dos módulos, mas permanece não ligada ao AUTO até validação sobre nuvem real.

`BINO_APOIO` é um repositório de investigação/referências, contendo também extrações e artefactos provenientes de Viizor, VRMesh e Global Mapper. O DEV Portátil **não copia automaticamente esse repositório, nem distribui executáveis, DLLs ou código proprietário extraído**. Incluem-se apenas algoritmos já implementados no Talude e recursos adicionais com origem e licença de utilização/redistribuição verificadas.

Dependências binárias instaladas pelo preparador: Python 3.12.10 (distribuição embeddable oficial), wheels pré-compilados de `requirements.txt`, Potree 1.8.2 e PotreeConverter 2.1 através do script original `studio/scripts/bootstrap_vendor.ps1`. O ZIP completo inclui os ficheiros de licença/aviso que vierem com as distribuições; antes de redistribuição externa, confirmar as licenças dos componentes efetivamente agregados.

## Regra de atualização

1. Implementações Python próprias ou open-source compatíveis: colocar em `src/` / `core/` com origem e licença documentadas. No ZIP completo entram automaticamente.
2. Ficheiros `vendor/` de dependências permitidas: verificar licença e juntar os assets requeridos antes de executar `CRIAR_ZIP_DEV_COMPLETO.bat`.
3. Material proprietário de investigação no `BINO_APOIO`: utilizar apenas como referência quando permitido, sem o incorporar nem publicar no runtime portátil.
4. Nunca modificar a baseline 1.1.7, reativar extrapolação terminal cega ou ligar R19/R20 ao AUTO apenas para facilitar o arranque portátil.
