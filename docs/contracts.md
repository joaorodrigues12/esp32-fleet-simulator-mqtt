# Contratos MQTT — versão 1

Status: proposta inicial versionada. O contrato deve ser revisado antes de implementar handlers.

## 1. Namespace e direcionamento

`vitals/{device_id}/{channel}`, com `device_id` obedecendo ao padrão `^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$`. Nenhum `+`, `#` ou `/` no ID. Um dispositivo é identificado por ID operacional estável; endereço MAC é metadado técnico opcional e não aparece no tópico. Nomes de pacientes e dados pessoais **não** são publicados.

| Tópico | Publicador → assinante | QoS | Retain | Payload | Motivo |
| --- | --- | --- | --- | --- | --- |
| `vitals/{device_id}/data` | Dispositivo → consumidor | 1 | Não | `VitalsDataV1` | Dados associados à sessão amostrada; podem ser duplicados |
| `vitals/{device_id}/status` | Dispositivo/LWT → consumidor | 1 | Sim | `DeviceStatusV1` | Snapshot online/offline para novos assinantes |
| `vitals/{device_id}/session` | Dispositivo → consumidor | 1 | Não | `SessionEventV1` | Confirma transição observada pelo dispositivo; histórico não é snapshot retido |
| `vitals/{device_id}/command` | Controlador → dispositivo | 1 | **Não** | `SessionCommandV1` | Evita replay automático de comando antigo após subscribe |
| `vitals/{device_id}/ack` | Dispositivo → controlador | 1 | Não | `CommandAckV1` | Confirma resultado de comando por ID |

`status` retido representa **último anúncio**, não prova de presença atual em qualquer instante; validar `occurred_at`/heartbeat. `data`, `command`, `session` e `ack` não são retidos. Para estado de sessão após boot, o controlador reconcilia com um novo comando de versão superior. MQTT 3.1.1 é o alvo inicial.

### Wildcards

- Consumidor de telemetria: `vitals/+/data`.
- Consumidor de status: `vitals/+/status`.
- Consumidor de sessões: `vitals/+/session`.
- Controlador de resultados: `vitals/+/ack`.
- Cada dispositivo virtual assina **somente** `vitals/{seu_device_id}/command`.
- `vitals/#` destina-se a debug local, não a ACL de produção. O `+` seleciona exatamente um nível, `#` seleciona subárvore.

## 2. Envelope comum

Mensagens são JSON UTF-8 de até 16 KiB (limite inicial de aplicação). Valores `NaN`, `Infinity` e numéricos fora da faixa declarada são rejeitados pelo Pydantic. Campos desconhecidos são rejeitados em contratos v1 para prevenir erros silenciosos; acréscimos compatíveis exigem incremento de `schema_version` menor ou nova política de compatibilidade documentada.

Campos comuns: `schema_version` (inteiro, inicialmente `1`), `message_id` (UUID), `device_id` (string), `boot_id` (UUID, criado em cada inicialização do dispositivo) e `occurred_at` (timestamp ISO 8601 UTC). Dados de leitura usam também `sampled_at` para preservar a hora efetiva da medição.

### `VitalsDataV1`

| Campo | Tipo | Obrigatório | Restrições |
| --- | --- | --- | --- |
| `schema_version` | int | Sim | igual a 1 |
| `message_id`, `boot_id` | UUID | Sim | IDs válidos |
| `device_id` | string | Sim | igual ao ID no tópico |
| `occurred_at`, `sampled_at` | UTC datetime | Sim | com fuso; `sampled_at` não muda em atraso |
| `sequence_number` | int | Sim | >= 0, monotônico por `boot_id` |
| `session_id` | UUID ou null | Sim | null quando em `IDLE` |
| `session_version` | int | Sim | >= 0; corresponde à revisão capturada na geração |
| `heart_rate_bpm` | int | Sim | 20 a 250 (limite de validação, não faixa normal) |
| `respiratory_rate_rpm` | int | Sim | 4 a 80 |
| `temperature_c` | decimal | Sim | 30.0 a 43.0 |

Exemplo:

```json
{
  "schema_version": 1,
  "message_id": "bfb2d0c2-7b0c-4971-81fa-afd309c76b57",
  "device_id": "esp32-001",
  "boot_id": "b0db56ca-12a0-4a6f-886e-e03dd41cab96",
  "occurred_at": "2026-11-20T18:00:02Z",
  "sampled_at": "2026-11-20T18:00:00Z",
  "sequence_number": 42,
  "session_id": "420ffcdf-9e93-4317-b263-63efc3e915b6",
  "session_version": 3,
  "heart_rate_bpm": 82,
  "respiratory_rate_rpm": 17,
  "temperature_c": 36.7
}
```

### `SessionCommandV1`

```json
{
  "schema_version": 1,
  "message_id": "454a9a10-3abf-431f-9b79-bb46244e4639",
  "command_id": "dad1b6be-1bf5-487a-9620-d96cecce8a3e",
  "device_id": "esp32-001",
  "boot_id": "b0db56ca-12a0-4a6f-886e-e03dd41cab96",
  "occurred_at": "2026-11-20T18:00:00Z",
  "expires_at": "2026-11-20T18:00:30Z",
  "action": "START",
  "session_id": "420ffcdf-9e93-4317-b263-63efc3e915b6",
  "session_version": 3
}
```

Validações: `action` em `START|END`; `command_id` UUID; `session_id` UUID não nulo para ambos; `session_version` não negativo; `expires_at` UTC posterior a `occurred_at`; `boot_id` deve corresponder à execução atual. Um comando destinado a boot antigo é rejeitado. O controlador obtém `boot_id` por `status` e reenvia comando fresco após reconexão/reinicialização.

### `SessionEventV1`

Campos: envelope comum, `session_id` UUID|null, `session_version` int >= 0, `state` em `ACTIVE|IDLE`, `cause_command_id` UUID. `ACTIVE` exige `session_id` não nulo; `IDLE` exige null. Publicar após transição aprovada.

### `CommandAckV1`

Campos: envelope comum, `command_id` UUID, `accepted` bool, `reason` enum `APPLIED|DUPLICATE|STALE_VERSION|WRONG_SESSION|EXPIRED|INVALID_BOOT|INVALID_PAYLOAD|CONFLICT`, `session_id` UUID|null, `session_version` int >= 0, `state` (`ACTIVE|IDLE`). ACK registra decisão lógica e não comprova persistência externa. Se o comando não for validável o suficiente para extrair `command_id`, registrar erro local em vez de publicar ACK sem correlação.

### `DeviceStatusV1`

Campos: envelope comum, `state` (`online|offline`), `reason` (`connected|graceful_shutdown|unexpected_disconnect`), `heartbeat_interval_seconds` int positivo. O LWT é preparado antes do CONNECT, com `boot_id` e `message_id` previamente criados e `reason=unexpected_disconnect`; hora `occurred_at` do LWT é **hora de criação do will**, e não necessariamente o instante da falha. O consumidor deve usar a hora de recebimento e timeout para interpretar presença.

## 3. Sessões, idempotência e ordenação

A fonte autoritativa de vínculo paciente/dispositivo e a restrição de uma sessão ativa é o VetMonitor, não o simulador. O simulador mantém apenas a sessão de referência em memória. `START` exige estado `IDLE`; `END` exige correspondência com a sessão ativa. Uma revisão (`session_version`) maior acompanha cada comando aplicado. Duplicação do mesmo `command_id` retorna `DUPLICATE` sem reaplicar efeitos. Revisões antigas são rejeitadas. O receptor deve deduplicar `message_id` e tratar leituras fora de ordem com `(device_id, boot_id, sequence_number)`.

A operação `END` encerra imediatamente a emissão de novas leituras associadas à sessão antiga; leituras já amostradas e atrasadas preservam o `session_id` antigo. Não há garantia de ordenação entre tópicos diferentes. Se uma confirmação for perdida, o controlador usa `ack` + consulta/reconciliação de estado no seu próprio backend; o simulador não fornece armazenamento durável.

## 4. Compatibilidade e evolução

A versão 1 mantém os tópicos `vitals/{device_id}/...` previstos inicialmente. O campo obrigatório `schema_version=1` identifica payloads. Alterações incompatíveis devem migrar para um namespace novo, por exemplo `vitals-v2/{device_id}/data`, e novos modelos documentados; os consumidores migram explicitamente, sem aceitar interpretações ambíguas. Mudanças aditivas somente se o consumidor estiver preparado e o contrato definir compatibilidade. Cada alteração de tópico/schema exige PR, testes de contrato e registro em CHANGELOG.

## 5. Testes de contrato mínimos

- Aceitação de exemplos válidos e rejeição de tipos inválidos, valores extremos e campos desconhecidos.
- Associação `device_id` do payload com o nível correspondente do tópico.
- START/END/START entre pacientes com versões crescentes.
- DUPLICATE, STALE_VERSION, EXPIRED e INVALID_BOOT, reconexão e novo boot.
- Preservação de `sampled_at` e `session_id` ao atrasar publicação.
- LWT em queda abrupta e publicação explícita em desligamento limpo.
- Ausência de replay de comando retido; QoS 1 e idempotência.
