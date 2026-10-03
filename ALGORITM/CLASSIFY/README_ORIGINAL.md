# TALUDE STUDEO — classificador totalmente autónomo V1

O ficheiro `classify_las_algorithm.py` **não importa LAS-CAFIISICA** e não requer
manto, grelha de evidências, seeds, breaklines ou qualquer módulo externo do projeto.
Lê LAS/LAZ original e escreve LAS/LAZ classificado diretamente.

## Instalação
`py -3.12 -m pip install -r requirements.txt`

## Execução
`py -3.12 classify_las_algorithm.py entrada.las saida.las --cell 0.35`

Também pode ser importado por outra aplicação:
`from classify_las_algorithm import classify_file, classify_arrays, Settings`

## Limites relevantes
Este é um classificador experimental independente **V1**, não é uma migração
completa nem uma reprodução comprovada do motor R20.3. Utiliza mínimos locais,
suporte espacial e filtragem conservadora de elevação. Não inclui reconstrução
fidedigna do manto invertido, modelo robusto de breaklines, fusão P1/L3 ou
identificação garantida de coberturas. Não fabrica pontos Ground.
Para nuvens de 241–318 milhões de pontos, esta versão ainda precisa de
processamento espacial por mosaicos: a função de ficheiro acumula XYZ em RAM.
Não executar no ficheiro original; testar numa cópia/recorte.
O parâmetro `seed_percentile` está reservado para evolução e não altera V1.
CRS de entrada obrigatoriamente EPSG:3763; o CRS original é preservado na saída.
