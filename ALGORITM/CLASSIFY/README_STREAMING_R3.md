# Streaming Ground R3 — API externa 1.0

A pasta externa permite substituir a implementação sem recompilar o Studio.

Contrato mínimo: `classify_file_streamed(source, destination, settings=Settings(), overwrite=False)`; opcionalmente `progress_callback(label: str, fraction: float)` e `cancel_callback() -> bool`.

O carregador `load_classifier()` mantém `API_VERSION='1.0'`. A função adicional presente no ficheiro ativo encaminha a chamada para `classify_streaming.py`. A implementação V1 original foi preservada em `classify_las_algorithm_ORIGINAL_V1.py`.

Não importar QGIS, nem fazer recortes arbitrários de blocos de 25 M pontos como substituto da superfície global. Os dois passos usam um único grid espacial global, com uso de RAM aproximadamente proporcional ao número de células e ao bloco LAS.
