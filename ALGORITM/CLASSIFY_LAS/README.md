# CLASSIFY_LAS — módulo do TALUDE STUDEO

Cópia integrada do **LAS-CAFIISICA R20.4 Universal Ground + MDT**.

- origem: `binopinto07-lang/LAS-CAFIISICA`
- branch: `r20-4-universal-mdt`
- entrada estável: `ALGORITM.CLASSIFY_LAS.api.classify_and_create_mdt`

Este módulo substitui qualquer classificador Ground temporário/simplificado.
Não substitui os algoritmos de deteção de CRISTA/PÉ.

Fluxo:
`LAS/LAZ -> Ground R20.4 -> nuvem classificada -> Ground medido -> MDT EPSG:3763 -> mapa Observado/Interpolado`.

P1, L3 e outras nuvens LAS/LAZ seguem o mesmo pipeline geométrico. Retornos
LiDAR são evidência opcional; 1/1 fotogramétrico não é prova de Ground.

A pasta deve ser tratada como módulo versionado: para atualizar o CLASSIFY LAS,
substitui-se o módulo por uma revisão validada, em vez de alterar ficheiros internos
caso a caso a partir do TALUDE STUDEO.
