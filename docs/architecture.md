# Arquitetura do ESP32 Fleet Simulator — MQTT

Status: proposta arquitetural para revisão; implementação de negócio pendente.

## 1. Objetivo e limites

O projeto simula uma frota de dispositivos ESP32 que envia frequência cardíaca, frequência respiratória e temperatura para um broker MQTT externo ou local. Ele é um repositório independente do VetMonitor. O simulador não persiste prontuários, não conhece dados identificáveis de pacientes e não oferece um cadastro de pacientes. A associação a pacientes ocorre no consumidor externo, como o VetMonitor, mediante sessões referenciadas por UUID.

**Metas de validação, não garantias:** 100 dispositivos, uma leitura a cada dois segundos, aproximadamente 50 mensagens de telemetria por segundo durante cinco minutos. Medir taxa de entrega, p50/p95, uso de memória, CPU e tempo de recuperação. O cenário clínico é sintético e não deve ser interpretado como dispositivo médico validado.

## 2. Decisões estruturais e alternativas

| Tema | Alternativa A | Alternativa B | Escolha e consequência |
| --- | --- | --- | --- |
| Concorrência | `asyncio` + `aiomqtt`: coordenação de tarefas com baixa sobrecarga de I/O | `paho-mqtt` + threads + `queue.Queue`: mais direto para bibliotecas síncronas, mais threads | **A**. Grande número de clientes e tarefas de temporização de I/O; demanda disciplina para impedir bloqueio do event loop. Ganho de performance requer medição, não é presumido. |
| Isolamento de dispositivos | Uma conexão/client MQTT por ESP32: identifica o LWT e falha de cada dispositivo | Um cliente compartilhado: menos conexões e recursos, mas LWT/falhas menos realistas | **Uma conexão por dispositivo**, com limite configurável e testes de recursos. |
| Protocolo | MQTT 3.1.1: compatível com a maior parte dos clientes | MQTT 5: propriedades avançadas, expiração e códigos de motivo ricos | **MQTT 3.1.1** no MVP; evolução controlada para MQTT 5 por ADR. |
| Modelo de payload | JSON + Pydantic: legível e fácil de inspecionar | Protobuf/MessagePack: menor payload, maior custo operacional | **JSON + Pydantic**, com `schema_version` e limites explícitos. |
| Dependências | Poetry com lockfile | uv com lockfile e distribuição moderna | **uv**, disponível no ambiente de montagem; `uv.lock` é gerado quando houver resolução de dependências. |
| Sessões | Autoridade no consumidor/orquestrador externo | Autoridade local em cada ESP32 | **Autoridade externa**. O dispositivo valida e confirma mudanças, mas não escolhe o paciente. |

Não há banco de dados nem interface web no simulador. O Mosquitto roda em Docker e não compõe o código Python.

## 3. Diagrama de componentes

```mermaid
flowchart LR
  CLI[CLI / SimulatorApplication] --> FLEET[FleetSupervisor]
  FLEET --> DEV[VirtualDevice x N]
  DEV --> VITALS[VitalsGenerator]
  DEV --> SESSION[SessionStateMachine]
  DEV --> CONN[MqttConnection x N]
  CONN --> BROKER[(Mosquitto)]
  BROKER --> ROUTER[SubscriptionRouter]
  ROUTER --> HANDLER[CommandHandler]
  HANDLER --> SESSION
  BROKER <-->|MQTT| EXTERNAL[Consumidor externo / VetMonitor futuro]
```

O `SubscriptionRouter` é instanciado por conexão e entrega comandos ao dispositivo correspondente. O broker não valida identidade do paciente; a decisão de associação é de responsabilidade do consumidor externo.

## 4. Pacotes e classes principais

A tabela representa interfaces e classes planejadas. Os módulos ainda não têm implementação.

| Camada / classe (caminho) | Faz | Não faz | Colaboradores | Ciclo de vida / estado |
| --- | --- | --- | --- | --- |
| Configuração: `SimulatorSettings` (`src/esp32_fleet/config/settings.py`) | Valida parâmetros e limites de execução | Não abre conexões | `SimulatorApplication` | Objeto imutável por execução |
| Conexão: `MqttConnection` (`mqtt/connection.py`) | Conecta, publica, assina e expõe eventos, com timeout e encerramento | Não interpreta payload clínico ou sessão | `ConnectionPolicy`, `SubscriptionRouter` | Uma instância por dispositivo; estado conectado/desconectado |
| Conexão: `ConnectionPolicy` (`mqtt/reconnect.py`) | Calcula backoff exponencial com jitter, limite de tentativas e critérios de falha | Não publica mensagens | `MqttConnection` | Uma por conexão; contador de tentativas |
| Conexão: `TopicCatalog` (`mqtt/topics.py`) | Constrói e valida tópicos e níveis MQTT | Não serializa dados | `MqttConnection`, `SubscriptionRouter` | Stateless, reutilizável |
| Assinatura: `SubscriptionRouter` (`mqtt/router.py`) | Associa filtros a handlers e distribui mensagens recebidas | Não executa regras de sessão | `MqttConnection`, `CommandHandler` | Uma instância por dispositivo, registro de handlers |
| Assinatura: `InboundQueue` (`mqtt/inbound_queue.py`) | Enfileira mensagens com capacidade definida e política de pressão | Não executa operações de negócio | `SubscriptionRouter`, `CommandHandler` | Uma por dispositivo; buffer limitado |
| Handlers: `CommandHandler` (`handlers/commands.py`) | Valida envelope/comando e solicita transição de sessão; emite confirmação | Não conhece paciente nem abre socket | `CommandSchema`, `SessionStateMachine`, `MessagePublisher` | Uma por dispositivo, sem estado clínico próprio |
| Handlers: `MessagePublisher` (`handlers/publisher.py`) | Serializa modelos validados e publica eventos/leituras com QoS apropriado | Não gera sinais nem escolhe sessão | `MqttConnection`, `TopicCatalog` | Uma por dispositivo, sem buffer durável |
| Domínio: `VirtualDevice` (`domain/device.py`) | Orquestra ciclo de leitura do dispositivo virtual e suas dependências | Não gerencia outros dispositivos nem o broker | `VitalsGenerator`, `SessionStateMachine`, `MessagePublisher` | Uma instância por dispositivo, estado operacional |
| Domínio: `VitalsGenerator` (`domain/vitals.py`) | Produz série sintética correlacionada por random walk com sementes | Não transporta MQTT nem identifica paciente | `ScenarioEngine`, `VitalsReading` | Uma por dispositivo, estado fisiológico sintético |
| Domínio: `ScenarioEngine` (`domain/scenarios.py`) | Aplica atraso, perda de conexão e anomalias configuradas | Não altera sessão sem comando | `VitalsGenerator`, `VirtualDevice` | Uma por dispositivo, estado de cenário |
| Domínio: `SessionStateMachine` (`domain/sessions.py`) | Aplica transições, revisão monotônica e idempotência de comandos | Não consulta prontuário ou banco do VetMonitor | `CommandHandler`, `SessionBinding` | Uma por dispositivo; sessão/revisão em memória |
| Domínio: `SessionBinding` (`domain/models.py`) | Representa referência de sessão, sem PII | Não consulta cadastro | `SessionStateMachine`, `VitalsReading` | Valor imutável por revisão |
| Domínio: `VitalsReading` (`domain/models.py`) | Representa leitura tipada, sequência e instante de amostragem | Não publica MQTT | `VitalsGenerator`, `MessagePublisher` | Valor imutável por amostra |
| Orquestração: `FleetSupervisor` (`orchestration/fleet.py`) | Cria/limita N dispositivos, distribui início/parada e recolhe métricas | Não implementa regras fisiológicas | `VirtualDevice`, `SimulatorSettings` | Uma por execução; tarefas e estado da frota |
| Orquestração: `SimulatorApplication` (`orchestration/application.py`) | Composition root, sinais de OS e shutdown ordenado | Não valida payload de comandos diretamente | `FleetSupervisor`, `SimulatorSettings` | Uma por processo |
| Observabilidade: `TelemetryMetrics` (`observability/metrics.py`) | Contabiliza publicações, erros, reconexões e latências | Não mantém dados de pacientes | `FleetSupervisor`, `MqttConnection` | Uma por execução, contadores seguros para event loop |

Interfaces internas propostas: `MqttTransport`, `ReadingSink`, `CommandSink` como `typing.Protocol` no pacote `ports/`; permitem testar sem broker. Estas interfaces não substituem os contratos públicos MQTT.

## 5. Concorrência: `asyncio` com `aiomqtt`

Usar um event loop por processo; cada ESP32 possui uma instância de cliente MQTT e tarefas de publicação, recepção e supervisão. `asyncio.TaskGroup` coordena tarefas com escopo de vida definido. O acesso a estado de sessão ocorre em uma única tarefa responsável ou sob `asyncio.Lock` quando múltiplos consumidores podem alterar o estado. Operações bloqueantes devem ser evitadas no event loop; integrações síncronas futuras usam adaptadores e `asyncio.to_thread` quando necessário.

A fila de entrada terá limite configurável; comandos não podem ser descartados silenciosamente. Quando a capacidade acabar, registrar erro, interromper processamento da conexão ou aplicar estratégia de rejeição observável. A fila de saída é limitada e não garante persistência em disco. QoS 1 significa entrega ao menos uma vez entre os participantes do protocolo, **não** entrega exatamente uma vez à aplicação. A aplicação usa IDs idempotentes e, quando necessário, reconciliação.

Reconexão: cada dispositivo supervisiona sua conexão; falhas transitórias acionam backoff exponencial com jitter e teto, cancelável no encerramento. Após reconectar, reconstituir assinaturas e publicar `online` somente depois da conexão estabelecida. Verificar a versão real da API do `aiomqtt` ao instalar: versões recentes podem oferecer reconexão integrada, mas a política e os testes continuam sendo responsabilidade de `ConnectionPolicy`. Evitar dois loops de reconexão concorrentes.

**Capacidade:** 100 dispositivos x 1 leitura/2 s = 50 publicações de `data`/s em média. O total de mensagens aumenta com status, sessões, acks e cenários. Medir 5 minutos, latências, CPU/RAM, perdas detectadas por `sequence_number`, duplicatas e recuperação. Uma solução com threads continua viável se um consumidor futuro depender intensamente de APIs síncronas; comparar benchmarks antes de mudar o modelo.

## 6. Ciclo de vida do dispositivo e da sessão

Estados de sessão: `IDLE`, `ACTIVE`. `START` aceito somente de `IDLE`, ou repetido de forma idempotente com mesmo `command_id`. `END` aceito somente quando `session_id` e `session_version` correspondem à sessão vigente. Ao encerrar, mover imediatamente para `IDLE`. A versão é monotônica e fornecida pelo orquestrador externo; comandos com revisão inferior/igual de outro `command_id` são rejeitados. Uma transição válida usa revisão estritamente maior, mesmo após voltar ao estado `IDLE`.

A associação entre `session_id` e paciente está exclusivamente no VetMonitor. O dispositivo virtual nunca recebe nome ou cadastro. A leitura captura `session_id`, `session_version`, `sampled_at` e `sequence_number` **no instante da geração**, não no instante do envio. Uma mensagem antiga não é reatribuída à sessão atual. Leituras emitidas sem sessão contêm `session_id: null`, úteis apenas para testes e não para prontuário. O receptor externo valida o vínculo e o intervalo temporal; o simulador não pode garantir essa verificação.

**Limitação de restart:** o simulador mantém revisão e sessão em memória. Reiniciar o processo elimina esse estado e não permite reconstruir exatamente transições anteriores. O consumidor deve reconciliar a sessão após `boot_id` novo. Não enviar comandos `START` antigos como retained. Persistência local e recuperação forte de sessão são extensões futuras.

```mermaid
sequenceDiagram
  participant Control as Orquestrador externo
  participant Broker as Mosquitto
  participant Device as ESP32 virtual
  Control->>Broker: command START(id, session, version)
  Broker->>Device: command START
  Device->>Device: valida, aplica transição idempotente
  Device->>Broker: session (estado confirmado)
  Device->>Broker: ack (command_id, outcome)
  Device->>Broker: data (session_id, version, sampled_at)
  Control->>Broker: command END(id, session, version+1)
  Broker->>Device: command END
  Device->>Broker: session (IDLE)
  Device->>Broker: ack
```

## 7. Gestão de falhas e segurança

- LWT publica `offline` em `status`, `retain=true`, QoS 1. Em parada limpa, publicar `offline` explicitamente antes de `DISCONNECT`.
- `status` é informação de conectividade; sessão clínica não se encerra automaticamente ao ficar offline. Timeout e alertas dependem do consumidor.
- Comando: autenticar dispositivo e controlador por credenciais distintas, ACL de publicar/assinar por tópico; TLS quando fora da rede local. Nenhuma senha em código ou repositório.
- Cada dispositivo usa `client_id` único e previsível dentro do namespace do ambiente; conexões de instâncias distintas não podem compartilhar IDs.
- Limitar tamanho de payload, taxa, dispositivos, duração da simulação e logs; nunca registrar senha ou dados identificáveis.
- Relógio: usar UTC e `boot_id` + sequência. Latência de rede e relógios divergentes são limitações medidas; consumidores não devem confiar apenas no timestamp emitido.
- Testes: schemas, transições, idempotência, reconexão, ordenação, duplicatas, atraso entre sessões, LWT, graceful shutdown e carga de 100 dispositivos.

## 8. Documentação das decisões

Ver `docs/adr/0001-asyncio-aiomqtt.md`, `0002-connection-per-device.md` e `0003-mqtt-contract-versioning.md`.
