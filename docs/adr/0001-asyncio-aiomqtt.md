# ADR 0001: asyncio e aiomqtt

Status: aceito para a primeira implementação. Data: 2026-11-20.

## Contexto

O simulador emula até centenas de conexões MQTT independentes, com temporização de publicações e recepção de comandos. O custo dominante tende a ser I/O e timers, mas não há benchmark do projeto ainda.

## Decisão

Utilizar `asyncio` e `aiomqtt` com tarefas supervisionadas, filas limitadas e operações não bloqueantes. Testar compatibilidade com a API exata selecionada no lockfile; evitar loops duplicados de reconexão.

## Alternativa

`paho-mqtt` com threads dedicadas e `queue.Queue` é mais direto para consumidores síncronos, mas aumenta o número de threads e exige coordenação de shutdown. A escolha poderá ser revista com resultados de carga e requisitos de integrações síncronas.

## Consequências

O código deve propagar cancelamentos corretamente e limitar filas. Não alegar ganho de latência sem medir. Testes assíncronos usarão `pytest-asyncio`.
